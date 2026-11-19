"""Public 24 GB demo: ComfyUI runs hidden behind the Director.

The usual ComfyUI address (the Vast "Open" button, the tunnel link) opens /director instead of the node canvas.
ComfyUI's own page is still there for fixing things:  <your address>/?comfy=1   (remembered for this browser
until it is closed; /?comfy=0 hides it again). DIRECTOR_SHOW_COMFY=1 turns the redirect off for the whole machine.
Only active when the PUBLIC_DEMO marker sits next to this file; it never touches /api, /director or any file route.
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
COOKIE = 'director_show_comfy'
ROOT_PATHS = ('/', '/index.html')


def active(env=None):
    env = os.environ if env is None else env
    return (HERE / 'PUBLIC_DEMO').is_file() and env.get('DIRECTOR_SHOW_COMFY', '').strip() != '1'


def decide(method, path, query, cookies):
    """-> ('redirect', '/director') | ('comfy', set_cookie: True/False/None) | ('pass', None)."""
    if method not in ('GET', 'HEAD') or path not in ROOT_PATHS: return ('pass', None)
    q = (query.get('comfy') or '').strip()
    if q == '0': return ('hide', None)
    if q: return ('comfy', True)
    if cookies.get(COOKIE) == '1': return ('comfy', None)
    return ('redirect', '/director')


def install(app):
    """Add the redirect to ComfyUI's aiohttp app (custom nodes load before the server starts)."""
    from aiohttp import web

    @web.middleware
    async def director_front_door(request, handler):
        what, arg = decide(request.method, request.path, request.query, request.cookies)
        if what == 'redirect': raise web.HTTPFound(arg)
        if what == 'hide':
            r = web.HTTPFound('/director'); r.del_cookie(COOKIE); raise r
        resp = await handler(request)
        if what == 'comfy' and arg: resp.set_cookie(COOKIE, '1', samesite='Lax')   # session cookie: gone when the browser closes
        return resp

    app.middlewares.append(director_front_door)
    return director_front_door
