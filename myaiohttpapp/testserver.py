import asyncio
from aiohttp import web
import asyncio_redis
import aioamqp
import time
import os
import json

WS_FILE = os.path.join(os.path.dirname(__file__), 'websocket-error.html')

@asyncio.coroutine
def handle(request):
    name = request.match_info.get('name', "Anonymous")
    text = "Hello, " + name
    return web.Response(body=text.encode('utf-8'))


@asyncio.coroutine
def wshandler(request):
    user_id = request.match_info.get('user_id', None)
    token = request.match_info.get('token', None)
    if not user_id or not token:
        return web.Response(status=401)
    redis = request.app['redis']
    server_token = yield from redis.get(user_id)
    # print(user_id)
    # print(token)
    # print(server_token)
    if token != server_token:
        return web.Response(status=403)

    resp = web.WebSocketResponse()
    
    ok, protocol = resp.can_start(request)
    if not ok:
        with open(WS_FILE, 'rb') as fp:
            return web.Response(body=fp.read(), content_type='text/html')

    resp.start(request)

    # request.app['sockets'].append(resp)
    request.app['sockets'][user_id] = resp
    # print(request.app['sockets'])

    while True:
        msg = yield from resp.receive()

        if msg.tp == web.MsgType.text:
            resp.send_str("Hello, {}".format(msg.data))
        elif msg.tp == web.MsgType.binary:
            resp.send_bytes(msg.data)
        elif msg.tp == web.MsgType.close:
            # request.app['sockets'].remove(resp)
            request.app['sockets'].pop(user_id, None)
            break

    return resp


@asyncio.coroutine
def ws_ping(sockets, seconds):
    while True:
        a = yield from asyncio.sleep(seconds)
        for socket in sockets:
            sockets[socket].send_str('websocket server ping')


@asyncio.coroutine
def sleep_handler(request):
    is_sync = request.GET.get('is_sync') == "1"
    seconds = int(request.match_info.get('seconds', 1))
    if seconds not in range(1,10):
        seconds = 1
    if is_sync:
        time.sleep(seconds)
    else:
        yield from asyncio.sleep(seconds)
    text = "Wake up after {} seconds".format(seconds)
    return web.Response(body=text.encode('utf-8'))


def callback_wrapper(channel, app):
    @asyncio.coroutine
    def callback(body, envelope, properties):
        yield from channel.basic_client_ack(envelope.delivery_tag)
        data = json.loads(body.decode('utf-8'))

        user_id = str(data.get('user_id'))

        redis = app['redis']
        authenticated = yield from redis.exists(user_id)

        if authenticated:
            socket = app['sockets'][user_id]
            if data.get('job_id'):
                socket.send_str(
                    '{}:{}:{}'.format(
                        'job',
                        data.get('job_id'),
                        'is {}'.format(data.get('status'))
                    )
                )

        for socket in app['sockets']:
            app['sockets'][socket].send_str('{}'.format(data))
    return callback


@asyncio.coroutine
def receive(app):
    try:
        transport, protocol = yield from aioamqp.connect(
            host=os.environ.get('RABBIT_PORT_5672_TCP_ADDR', '192.168.59.103'),
            port=int(os.environ.get('RABBIT_PORT_5672_TCP_PORT', 5672)),
            login=os.environ.get('RABBIT_ENV_USER', 'admin'),
            password=os.environ.get('RABBIT_ENV_RABBITMQ_PASS', 'password'),
        )
    except Exception as e:
        print("closed amqp connections")
        return

    channel = yield from protocol.channel()
    exchange = yield from channel.exchange_declare(exchange_name='ws_msg.exchange', type_name='direct')
    queue_name = 'ws_msg'

    yield from channel.queue_declare(queue_name)
    yield from channel.queue_bind(queue_name, 'ws_msg.exchange', 'ws_msg')

    # yield from asyncio.wait_for(channel.queue(queue_name, durable=False, auto_delete=False), timeout=10)
    yield from asyncio.wait_for(
        channel.basic_consume(
            queue_name,
            callback=callback_wrapper(channel, app)
        ),
        timeout=10
    )


@asyncio.coroutine
def setup_redis(app):
    # Create Redis connection
    connection = yield from asyncio_redis.Connection.create(
        host=os.environ.get('REDIS_PORT_6379_TCP_ADDR', '127.0.0.1'),
        port=int(os.environ.get('REDIS_PORT_6379_TCP_PORT', 6379)),
        db=int(os.environ.get('USER_TOKEN_DB', 1)),
    )
    app['redis'] = connection

    return connection


@asyncio.coroutine
def init(loop):
    app = web.Application(loop=loop)
    app['sockets'] = {}
    app.router.add_route('GET', '/api/ws/{user_id}/{token}', wshandler)
    app.router.add_route('GET', '/sleep/{seconds}', sleep_handler)
    app.router.add_route('GET', '/{name}', handle)

    srv = yield from loop.create_server(
        app.make_handler(),
        '0.0.0.0', 8000
    )
    print("async server started at http://0.0.0.0:8000")

    loop.create_task(setup_redis(app))
    loop.create_task(ws_ping(app['sockets'], 2))

    return srv, app


loop = asyncio.get_event_loop()
srv, app = loop.run_until_complete(init(loop))
loop.run_until_complete(receive(app))
try:
    loop.run_forever()
except KeyboardInterrupt:
    loop.close()