"""
Mighty Self-Serve
=================
Personal authorization layer for AI agents.
Self-contained Flask app — SQLite. Requires: flask, cryptography, bcrypt, py_vapid, pywebpush (push notifications optional).

Local:   python3 app.py  →  http://localhost:5004
Railway: set start command to  python3 app.py
         PORT env var is picked up automatically.

Env vars (all optional):
  SECRET_KEY    — Flask session secret (generated randomly if not set)
  DATABASE_PATH — SQLite file path (default: mighty.db)
  BASE_URL      — Public URL override (e.g. https://mighty-selfserve.up.railway.app)
  PORT          — Port to listen on (default: 5004)
"""

import os, io, csv, json, re, secrets, hashlib, sqlite3, threading, urllib.request, urllib.error, html, time, base64
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from cryptography.fernet import Fernet
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False
import bcrypt as _bcrypt

from datetime import datetime, timezone, timedelta
from functools import wraps
from flask import Flask, request, jsonify, session, redirect, g, make_response

def he(s):
    """HTML-escape a value for safe insertion into HTML."""
    return html.escape(str(s)) if s is not None else ""

app = Flask(__name__)
_secret_key = os.environ.get("SECRET_KEY", "")
if not _secret_key:
    if os.environ.get("RAILWAY_ENVIRONMENT") == "production":
        raise RuntimeError("SECRET_KEY environment variable must be set in production")
    _secret_key = secrets.token_hex(32)
    print("[Mighty] WARNING: SECRET_KEY not set — generating random key. All sessions will be lost on restart.", flush=True)
app.secret_key = _secret_key
app.config["SESSION_COOKIE_HTTPONLY"]    = True
app.config["SESSION_COOKIE_SAMESITE"]    = "Lax"
app.config["SESSION_COOKIE_SECURE"]      = os.environ.get("RAILWAY_ENVIRONMENT") == "production"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# ── Simple in-memory rate limiter (per-IP, no external deps) ─────────────────
_rl_store: dict = {}
_rl_lock = threading.Lock()

def _rate_limit(ip: str, name: str, limit: int = 10, window: int = 60) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    key = f"{ip}:{name}"
    now = time.time()
    with _rl_lock:
        ts = [t for t in _rl_store.get(key, []) if now - t < window]
        if len(ts) >= limit:
            return False
        ts.append(now)
        if ts:
            _rl_store[key] = ts
        else:
            _rl_store.pop(key, None)  # evict empty buckets
        # Periodic cleanup: prune stale keys every ~1000 requests to prevent memory leak
        if len(_rl_store) > 5000:
            _stale = [k for k, v in _rl_store.items() if not v or now - v[-1] > 3600]
            for _k in _stale:
                _rl_store.pop(_k, None)
        return True

DATABASE        = os.environ.get("DATABASE_PATH", "/app/data/mighty.db")
PORT            = int(os.environ.get("PORT", 5004))
TIMEOUT_SEC     = 300  # pending authorization expires after 5 minutes
POSTMARK_API_KEY = os.environ.get("POSTMARK_API_KEY", "")
POSTMARK_FROM    = os.environ.get("POSTMARK_FROM", "Mighty <noreply@mighty.ai>")
NOTIFY_EMAIL_OVERRIDE = os.environ.get("NOTIFY_EMAIL", "")  # override recipient for sandbox testing
if NOTIFY_EMAIL_OVERRIDE:
    print(f"[Mighty] WARNING: NOTIFY_EMAIL_OVERRIDE is set — all notification emails go to {NOTIFY_EMAIL_OVERRIDE}", flush=True)
ANTHROPIC_API_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
MS_CLIENT_ID         = os.environ.get("MS_CLIENT_ID", "")
MS_CLIENT_SECRET     = os.environ.get("MS_CLIENT_SECRET", "")

LOGO_ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAADkAAABQCAYAAACj490XAAABWGlDQ1BJQ0MgUHJvZmlsZQAAeJx9kLFLw1AQxr9WpaB1EB0cHDKJQ5SSCro4tBVEcQhVweqUvqapkMZHkiIFN/+Bgv+BCs5uFoc6OjgIopPo5uSk4KLleS+JpCJ6j+N+fO+74zggOW5wbvcDqDu+W1zKK5ulLSX1jAS9IAzm8Zyur0r+rj/j/T703k7LWb///43Biukxqp+UGcZdH0ioxPqezyXvE4+5tBRxS7IV8onkcsjngWe9WCC+JlZYzagQvxCr5R7d6uG63WDRDnL7tOlsrMk5lBNYxA48cNgw0IQCHdk//LOBv4BdcjfhUp+FGnzqyZEiJ5jEy3DAMAOVWEOGUpN3ju53F91PjbWDJ2ChI4S4iLWVDnA2Rydrx9rUPDAyBFy1ueEagdRHmaxWgddTYLgEjN5Qz7ZXzWrh9uk8MPAoxNskkDoEui0hPo6E6B5T8wNw6XwBA6diE8HYWhMAABekSURBVHja7ZxpkxzXdaafczNr6wWN3tALGsROEgQBmEMJlqghJdoirM0TCstiSLYZ0ihiJhQzf2L+hCPGMxIlyzPaLMkbRS2URGMsLiLFRQCIhQJANtDd6BW9VXV1Vea9xx9uVtbeACMc/uBxIToaqMrKe88923vecxIyOXlC+Tf+Mvx/8Pp3If9lX9LwVwPGgMi/ysph8z5aZNbmvaXvte655aOOIioggoogmlxpDOp2+GaHtRrX1Jav1s5M76RJuZMepF0q3Wk/2rADEbAxkgmRbBZ1DkEREX/bbqeknc9BdtiwNPyE3fbUumNt0Gwqo3Q//NbjVY0RE1IY28foqQ9h8j2sXnmDzesXcdVtJAxREXCart1NM2372cHqtZOQ0kEg7WAO0uV7AmjtL5K865RM/xCD9z7E+OknGDl2mjDfy/rJC8yc/RuWL75KdW3Fm7ERUEU1WXcHTSF1JUvLxlTr+5WmPClmRyE7HkZyrUgiHCDGAIqzMWG+l969Rxl96PeYfN8ZCkN7sDZCxBBmhWh7laULLzL30k9YvXKOuLSJCTP+3k67xoRWLaeHS7O1tQkpYtp8644CNp2gJItZJAjJD04ycvwx9jx8hr6JIwSZxHACxYSCCSDIChJYSgvTzLzwLAuvvUDp1g1cpYKEYXI/7Ww+XQJU6/46arJbgK2ZgLZ8IOD9CQXnyPTspm//KcYf/iQj938Qk+1FXYwJHCZnMNkMQQhIDDhMIARZg7oKK1feYPq577F8/jWqW0VM7eATNQk7a5BWl+mmyU5a7Ga2NTtVFxPm+ukZv5/RE48zfOz3yO0a8Z+JEmSFTE8OpUT59gxBKPSO7yXMFVAXgzhMKAShUF5bZO7V55l98edsXL1MtLVFkA1BxWv1Pb6C/v6x/9G4YblDihKtZQMfoFUtxoTkB6cYeeAJ9n7wKQaPfogg14c6RQSCXIgJlWpxkaULZ3n3p3/FyuVfoUB+aJBsTy9iAhRFMWT7+xk+cozBw/fiXERlfRW7vQ3OIYFp31dD/JAO+243V/WpQbqkBanFbHWoKpnCELv2/QdGj/0BAwfe500TC4ES5A1hNiSO19icOcfcr59l7e1XsaV1ECHs382ehx7lnsc+ztC9xwlyPahzqLMEoSHIB1RLq8y//jI3f/FDbl88R7UWmLTBTjsEI9F69G3XpNDV9r2IinMRQbaX/rGTjJ/4DBO/8zl69zyASIg672NhTw4oU1y4yNwrf8fN//dNNt+5gFYrSX4VNKqwOf02a+9cJqqUyA30kesfIMhkAYdaCHN5Bg8dYuj+45h8gai0SbS5gYuqmDBodsQOOV5ahRSReq5r1R7ig4QE5PsnGT38BJO/858ZOvAhTKYPVYcYCLIBJuOIt5dZvvQ87/7iK9x+6yxxcb0We9uwRnX9NiuXf0Nxboaw0ENhZIQwnwdRRGNAKAwOM3r8JLsOHCQul6is3sZWKkk6bk3oLfK2Bp72tOFNU4AwP8rA+MPsOfQH9E+ehGwvikNCMPkAkzPE0Qobc2+wcP7HbNx4E7u14bXWFAbb8WbtMHNDo4x/8CMcePwMuw/dR5DLg7M465BAMKFQWV/h5ktnmf7Zj9m4+jZxqeQRUy0Kt6SbjoFHaM55YdDLruET7Dn4GfYcfpLC8H2ICcHhI2I2C5QorVxi/jd/w9wr36I4exEXVXaEXNIBRtpyifVrl1h79ypOlMLQIJnePoTAX61K2NPH0JH7GL7vXlRge/U2UbkMziKB8YGxQavNQtbEE7xpmgyFwhQjkx9n6ugXGRz7ACbsxTnr0X0mxASOSnmWpd/+jBsvf4XVay9it4t1QK7vrbIgwa/llSVWLv6GzVtzZHp6yY8MkynkfSq2DjEBfXvGGX3wBIWJKaLSJtW1VWy14iOH1KNPmyZry2UyY4yMfISpQ08xNHYGk92DU0WMEoQhEhriaIm1uReYffOvWLr8E6ob84CiXbKqdKpipLEUa8TJgkZVijfeYeXKBarFdfIDveQHBzHZPDhHXIkIslkGDx5m9PgJgp4ettdWicol1NrUGlsCj0FE6O+5j/GxzzM28SS9u45400zgTpDJIEGR0tol5t7+a2699W22lq6gtprUiuxcEkiXum4Hs46LG6xcPM/6O9cJslnyQ8NkCznE1JWS3z3IyP3H6Z86QGl1ia1bc92EFATD1MTnGB/7FBL0Yq3FGEOQCxFTpbx1jaWbP2Tm4tdYn38NF5V98dutPGmtRXe4rpvjSoKqtpcWWXzrHJu3Zsn19dA7NkKQK4BTXBShxrB7/yHicoVbr/0KcRaVtlKrllyzqAaI1AzPEVcWWV97lfmZZymuX8bGxWSnpqH86GCGd2BCdqog0oNTX404lGhtlZmf/YiNq5e454lPMPXIo/RPTGHCEGcVZzVlA7Qj/YHPk9bG2NhhDIRZKFemmbv5TVYW/5E42gACkKABtXcX4m6qGqW98NU7VBob777DW1//CxZe/zXH/+xL7Dn5EBpZNMG3DpeuaTozFQIqOAdGlMrW22ysvUEcbXj/FE2OXu/a7PQOHJB2uE60U8XhES7GoFHMysXzLL31FmpBbZwU3s0kmWnXo3dX8QAHa8FaC2p3pAaE7uhK7ux2Ha9R2YE3cn4THqUpuBY+RruxdenniaoTd3NWk8JV70oT0vK7mzvqXbB8bWs03lQdThW1DtXkqMTciXeVln8JgQFjNJFP26BYx7hFd3dtNT/pkEmkkbPR2i9J36unqYRzcV4B6qNTmzJMp+UbFxEFgzQJprUMpAml2BJKpYVokh18r9VXtSGgNoMURT1J2zE8qUuJiTYaMeykydQyVdvZIwGcUBg4jDEZysVpbFT0rIJ60KjaHmE70hY7kdiNJ+VigmyevgMHiatVStPXEQmb9utZg872b7qGAgVRSQOpX9MgBIgx9A4cZ9+JLzN66FNkekaSWlTbqbL30ESQFgxbey/Tv5t9H/0Yp7703xk78ZDHrtL8De0Yvv01YecFNf26cyn49+YiLrXjfN89TI0cp3f4CIvX/pbi4mUfkhv01JYftYNPSyMYENSAWs/47TpwhKnHP8HhMx8Do9w8+3xyd20KAqqgTnwqaVFp2BmMJCfjmjB701UGwTghCHcxtP8MhcH9LFz9e9ZmXiLeWklqSKlHjmYjacfp4oOLNx3I9g8wevL9HPz4Zxk+eoKwkKO6eTs9JFFp8G+tcdg4p6i9ixQiNPCOXeK7jV2iZQsuQ9/oCfIjByj89jBLl/+W7dVpf7wm8KelO7QUavnKWUQMhfEp9n34D7nnw58gPzjqSzvr/L5M4AMqDlTTAKkuWSaNrtoN1tWBZCPX2sneRTyjL2pAHXEEZHcxeuyP6B07wvz577Nx8zVstegxsJgkzLf7niaRMyz0MfTAw+z/6GcZvvcUJpPFVT2tohicepKrTmRLg6n6H+cE55oTU9g18Tp/uAYwYto4QGOCtDpwDsQJWJCwQP/4+wj79rI8+hNW3/4x1bUZnLNIgi7SCC+COosJs+RH9jJ++mPs/cAZevbsTdp6noJUHOoUdc2+o22YsZH51m5Cal1zUjsZrfMmDS+n3lzVCWK8j6qCRhanSm7XFHsf/lMG9h1j/vXvsD79JsSV5ipDlSCbZ/De0+x97EkGj5wizOVxVYWMI8j4e4rTxAwbq566IB6VCWoVAm1IttLdXFNBjaBOsFbbyBh13h1xHknV8moa2q1CJs+uqdNkBsYoXHqO5XPPEG0sQFKE54b3MfbwJxk79TiFkX0IIRq7ZFc18tqDDecSeGldc4IQfOVRM9eGgFRjX0yHkONvbvEn47RjtS+t4VEVwRKIF1SdotbirNAzfIip3/0T+vadRH2MR02GkZOfYuqRPyW3ez8uNjhrffgXXw1aGycHLuCkCQVJeggerGtN29aDFZ8IvUpNx5IoCRB1TG7agJeq/3HJTxBAaekNVqbPUi0vYQLjE40CsWIkiwlyyZIOCTJkCkOIFHCRTekVCYTqxhKL519ic+Y6gsFZh6rz2lJpdkNVnzYS1/HXcAfs2goqd0IvrukkKK9NM/vm08yf+z9U1qeTkOe1YGPr672mm9okuXlfE2JKC9e59uzTXH3mq2wt3AQEF9vEsurxpbUXog1RFrfTYAT1Zk7KJroupDD1pOyc4mKHSEC8vcrCW99ja/U6Yyc/y+4Dp1EKCTSs/1F1uNji1HO3zpZYPvci8y9/n41rb5AZHMfaJKA4g1qpC9AC3oXkcwsa1hCPdhey5qgmafz4BKst5L7WI7WrnawkBZFBbcTm3OtExQW2Vp5g5MFPkusbThl6UN+NFoOqY2vxHRbP/5CVC89TXZtH4xiRwBuTVZylTcDG9FELgrXg01rAhc0CSoP9Sv0Grh0MpOaj/kBwpL5TK1zL6zeYe/UvCQr9jJ/6NCIm4V4CUMUEIfHWGrMvfovlN5/xkdQYVHyOxIKz/r4uTSM0E1WqOJcUzw0CvyefFONnAIROlFdyXs5HOJ8tpXacPmhUt4i3NrCRbSqifYsOqsUNyqu3sNG2157W87WLExzqTHNQqeHcBuBSy+mp1uHOiCdF8AYyQfv0lCREsri04ZXANk3xoCY7EhP480tyXEoVOU9bGAn8gTjXvExqSZJaTl0X2pxKXP2wW3kV05Ylaz2EJrqkwVZr6EcFSYJBKqxrBg2SUG6CEiQBrWZW4LvQYqSWlDrRM4n5eT41btBSOkFTS9iu0Sebi9Sw2/yIJEfqHMSxraOZJFGr8wtLzdFVfJHdhYvUFk7Dl2FmR65SE1rUox0vrNbGXhomNLQGBiy+Fm1h0TqSyykqIbm5mvRYaqCcmvZqtZvtnGo8TEx8pfEQUuG1BYtKs9FYb67UAkprNYRJEVpN8y529cmujmydQjXeIrZxQi4L2cwAxmR8qaPOpxkRjPrKQ2MFq2m0bVWnOsFFpD6adq0a6c4mFFOL4ILGPgA5i1dTqn3/PZPNkyn0e6uzYKOIeLsENurMoNd0XIluY+Mo7fMVcuNkwv56zlBvHtZqImAC1FtYMm1CRjXCvVboaju7o42suSQukSwbJynKaVMHPNOzm/zAKLXQG1dKVDdW0DhKXKIjJemoxnOobCESYK2QMRNkM5OIyaT+YK3gIp/LREEsadtdkOZ0rIpJqE1t1VgjOmkkXWt8ai09WMVGFmdtugd1jp6hCXpGDhFHPsxH5XUq6wtNcaUDQFcq8QyV6kqyjiIU6CscIxPuSgXBVQjU+mI5AmKpB95mmimlK522r9WxSdBIarkkXzoDGtT7qImpFiaPkts9gYtBJGR7dYHS4o3UHbo0fAyRXaa0/RaOIibRWm/+IbLhXl+lA1vbF9jYOEe8XQYrYB1GTRPznebDRFsizdRHWh41BpvW9BHXQD5s3LzI5twVMAFOLT1jh9l96CFMECaByVK8dZ3t5ZmGIreDuQqCdVXWyy8RR8uozRBFlp7cQUZ2P0YmGAAXU6nOMbvwdVbXn0O0CtbgqrZpLKzRJ0U7MenG16y15NZCpWvsQAKMgeVLz3Pj+acpL1xLeK+A3UdPs2vqJHE5QjIh2xvzbF4/j1bKnkDrLGSNHnRsVi6wXjyPiyugYMgyOniG/t5TydeE7egmc4tfZ3HpR0SVjQb3ap/G9alAmzOAa6ZjmtoLtcar22bx4s+58fxXKc9eToKepX/fCYYfeBwTFnDWa3v9xiVWr7/e1vUOO44DC1hXYrn4LIXM/RR6jmBdhDET7Nn9earRKsWt8xgJqVRmmb31vxFTSUq0MDFNadJkLceJtFAorpmYkgaiDJTFc88y+09fI9qcR4xnD3om7mPvI5+jb+IY8XZMmMtQXplm+dxZovWFBAO7boP2dYwuYihWL3F76zmmevdg6COKLH2FE+wb+xI3F/6C4tYVRAyxXWFu/ttkwkFsXGoIKtoYtOvIp7EYaJmv1sRlXLTNwpvPEK3foro2gwQZ1DnyoweZ/NBT7D76KC4CCQzOVli+8DyrV37pXaAWHJNy0bRqst5nVVTLrJR+xFrxl9i4klQChv78w0yO/Ff6Cyd918tkqVYXKG1dxmop4VhdkxmaViJeG4Z1GwG1c6gYbHmV0vSrRGszSBCizlLYc4S9//ELDB79MBoFKcN/++qvWHjth7jyRlOfWDuZa+toNgjVeJGby09jXYah/o+ACM4F7Or5AMFYL7dWv8HG5isY8Wm+0zxqDblJY5WSBhdtGq5PDdbZxOQFnKV/6hQTj36BwYO/i7oAtY6wN8fau68w84/foLw07cdJO6wf7jRro4lvbcfT3Fr/BiaE4f5HcOSIHfT2PMg9+S8zG/Sztv5LVLebpjWaLKTxsYnGtWyn9TXF0SLCrsOPMPnIU/TvPYmzgWfvQ+H21ZeZe+Ev2Zq90JCq2luDYVuPvjVhq/okW32bmeX/iXWbDPZ+FEwvzim5zFH2Tn6ZTG6Y5eVnsPEmaqR5SF59tdJao6h2OWDfF0CCDMPHnmD8/Z+jZ+SQT/jGEWQD1t55mRtnv8LW3GUPULRL77NrL6SlW6oJMbJdvcnM8tNUq6uMjXyaIBjCaUw2M8nY2J9hgn6Wlv6OuDrvaX5taMtLQ4u8re1cH3KofRDkdjH0wMeZeP+T5AYmsVWHCQ24MvNv/pz5V79LZel6Mlzc/ZmRnZmBtuFeRSQgihdY3PgeGhQZ3f0ZguxebBxhwiFGxj9LtmeAxbm/plx6FyMGK4IxNb6omSwKDISB+EAlNfjnyPbvYfjYHzJy4tOEhRHicuwH8e0qSxd+ytwr3yVan02u186Tl9JNkx0a/E1TI6qIZIjsKvMrP6BcWWJ89Iv09h/BEROGfQxPfJJMYZjZ61+lXLyCMQYbw/a2J4FNQ6llksJX07xpyQxMMvHwFxi+9wwS5rHVmCAfUt2aZ+GN77J87h+Iy5tJLtTu7cC74ni69PE14SBVy6wX/wmVMpPhn1DoPYm1FrFZ+oY+wD25PPPT32Z98SVff5pkaFhM6ptxDFHVodYhqvSPn2T0xB+za/+jID2ojQlzIdtr15n/9be4ffkXxJUNn1JSsqzbMyr1z8KuEy6y06MSmtCKVTY2XyaOVxgd/TwDI4+BZhAC+oZPM5EdAMmSy+fI5yEMg9QyNKlJnfO1Wv/UaSbe/1/oGz/li2zrTXRz7nXmX/8mG+++iovKGBPW82vrBqV5iEjvpEm5w6iYS4tfR7F0iWr8v7BmndHJM5hgF1HkKPTfxz0P/jfCLIlADnGggaYt9Fwux/DhJ8gN7qdn9EFsBGEGCCJWr73ArTf+L+WFy/6pIQmgZZpWOz7Ss5O5au1pnfanZjqZco20EBGqlRvMz34Dq2sMT/4nMj17EBy5noOYjBLHRVwcpaW/iCEwSq5vD9nC70OQ85NtRrHxJrfffo6F3/yAytp0Atbv8ODLDj2bsNs8205Pu6m0w0CRkGplgfmb36FSWWLq6BfJ5fYTRdtYZ9IHRGtJPjBBQpfkExRlyRQyxNsL3Dr3bVauPEu8teo5W2h/OE06+KE29yVrl4WdJvt1h8eWtEPDVpJcioS4eJPVxZ/jXInRfU9SGH6QQCEIBGNMOjmiaokjh40cIo4gE7C1cpmli99n5erPcNViAtPc3Q/fSceytPtDonoX92ufqFKQAGfLrC6epbq9zLh7iqG9p73+Em1owzSDSQjmjVuvsXjxmxRnf42zlToO1Z2xyo7ZgC5Pwt5JqDs9T1krlRBHaeMCs1f+HOdWGRh/zDdQ1fqBYAUJA5CY1Xd/ysLF71C+/ds0wKjqjkPCXd/QOwQevTtraBa001MR6hurglIuXmP2ytfY3logqm6AyfiDCEKi7dssXPgWK9d+QHVzpj4ngN5VQOmU40TaZ/naHkjrdBo7Df9JlznXFC2JQV1EmN0FCDYqeUAd5MgURrBREVtZ9XRnrUn0Hmbzuj4415ABwo72Lu31oHQxGW2tKqQFO6pDJPRDS2kvUFBXoVq8kcyzh80BRjth5/c+kdi51GqbJ92hLLrTCHNbm940NH1qsT6oDwy1erzenXB6F++H/Cu9Oh6g6g528S/3+vf/++PfyuufAV46Ye0PF1AWAAAAAElFTkSuQmCC"


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    _db_dir = os.path.dirname(DATABASE)
    if _db_dir:
        os.makedirs(_db_dir, exist_ok=True)
    with sqlite3.connect(DATABASE) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                api_key       TEXT UNIQUE NOT NULL,
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS actions (
                id             TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL,
                action_type    TEXT NOT NULL,
                label          TEXT NOT NULL,
                fields         TEXT,
                status         TEXT NOT NULL,
                outcome        TEXT,
                approval_token TEXT UNIQUE,
                created_at     TEXT NOT NULL,
                decided_at     TEXT,
                expires_at     TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_actions_user  ON actions(user_id);
            CREATE INDEX IF NOT EXISTS idx_actions_token ON actions(approval_token);
            CREATE INDEX IF NOT EXISTS idx_users_key     ON users(api_key);
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                subscription TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions(user_id);
            CREATE TABLE IF NOT EXISTS enterprise_leads (
                id         TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                email      TEXT NOT NULL,
                company    TEXT,
                message    TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS password_resets (
                token      TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                created_at TEXT NOT NULL,
                used       INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS account_data (
                user_id      TEXT NOT NULL,
                source       TEXT NOT NULL,
                display_name TEXT NOT NULL,
                icon         TEXT,
                color        TEXT,
                data_enc     TEXT,
                synced_at    TEXT NOT NULL,
                PRIMARY KEY (user_id, source),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS pending_2fa (
                id             TEXT PRIMARY KEY,
                user_id        TEXT NOT NULL,
                source         TEXT NOT NULL,
                account_name   TEXT NOT NULL,
                challenge_type TEXT NOT NULL,
                message        TEXT,
                code           TEXT,
                status         TEXT NOT NULL DEFAULT 'pending',
                created_at     TEXT NOT NULL,
                expires_at     TEXT NOT NULL,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE INDEX IF NOT EXISTS idx_2fa_user ON pending_2fa(user_id);
            CREATE TABLE IF NOT EXISTS account_credentials (
                user_id      TEXT NOT NULL,
                source       TEXT NOT NULL,
                username_enc TEXT,
                password_enc TEXT,
                extra_enc    TEXT,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                PRIMARY KEY (user_id, source),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
            CREATE TABLE IF NOT EXISTS site_paths (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                site           TEXT NOT NULL,
                path           TEXT NOT NULL,
                reporter_count INTEGER DEFAULT 1,
                last_seen      TEXT NOT NULL,
                quality_score  REAL DEFAULT 1.0,
                failure_count  INTEGER DEFAULT 0,
                UNIQUE(site, path)
            );
            CREATE TABLE IF NOT EXISTS extraction_hints (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                site           TEXT NOT NULL,
                path           TEXT,
                trigger_phrase TEXT NOT NULL,
                field_key      TEXT NOT NULL,
                field_label    TEXT NOT NULL,
                neighborhood   TEXT,
                confidence     REAL DEFAULT 0.0,
                success_count  INTEGER DEFAULT 1,
                last_seen      TEXT NOT NULL,
                UNIQUE(site, trigger_phrase, field_key)
            );
            CREATE TABLE IF NOT EXISTS field_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                source      TEXT NOT NULL,
                field_key   TEXT NOT NULL,
                field_label TEXT NOT NULL,
                old_value   TEXT,
                new_value   TEXT NOT NULL,
                changed_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_fh_user ON field_history(user_id, source, changed_at);
            CREATE TABLE IF NOT EXISTS field_observations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                source      TEXT NOT NULL,
                field_key   TEXT NOT NULL,
                first_seen  TEXT NOT NULL,
                last_seen   TEXT NOT NULL,
                seen_count  INTEGER NOT NULL DEFAULT 1,
                UNIQUE(user_id, source, field_key)
            );
            CREATE INDEX IF NOT EXISTS idx_fo_user ON field_observations(user_id, source);
            CREATE TABLE IF NOT EXISTS approved_domains (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                domain      TEXT NOT NULL,
                approved    INTEGER NOT NULL DEFAULT 1,
                added_at    TEXT NOT NULL,
                UNIQUE(user_id, domain)
            );
            CREATE TABLE IF NOT EXISTS privacy_audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                event_type  TEXT NOT NULL,
                source      TEXT,
                domain      TEXT,
                detail      TEXT,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_pal_user ON privacy_audit_log(user_id, created_at);
            CREATE TABLE IF NOT EXISTS field_candidates (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                source      TEXT NOT NULL,
                field_key   TEXT NOT NULL,
                field_label TEXT NOT NULL,
                field_value TEXT NOT NULL,
                confidence  REAL DEFAULT 0.0,
                source_snippet TEXT,
                discovered_at  TEXT NOT NULL,
                status      TEXT NOT NULL DEFAULT 'pending',
                UNIQUE(user_id, source, field_key)
            );
            CREATE INDEX IF NOT EXISTS idx_fc_user ON field_candidates(user_id, source);
            CREATE TABLE IF NOT EXISTS reminder_snoozes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL,
                reminder_key TEXT NOT NULL,
                snoozed_until TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                UNIQUE(user_id, reminder_key)
            );
            CREATE TABLE IF NOT EXISTS intent_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      TEXT NOT NULL,
                intent_type  TEXT NOT NULL,
                page_url     TEXT,
                benefit_count INTEGER DEFAULT 0,
                benefits_json TEXT,
                detected_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ih_user ON intent_history(user_id, detected_at);
        """)

        # Pre-seed with known-good paths (quality_score=5 → treated as trusted immediately)
        _KNOWN_PATHS = [
            ('delta',      '/my-profile/certificates'),
            ('delta',      '/us/en/my-account/eCredits'),
            ('delta',      '/us/en/my-account/wallet'),
            ('delta',      '/us/en/my-account/companion-certificate'),
            ('delta',      '/myprofile'),
            ('marriott',   '/loyalty/myAccount/certificates'),
            ('marriott',   '/loyalty/myAccount/benefits'),
            ('hilton',     '/en/hilton-honors/guest/dashboard/'),
            ('hilton',     '/en/hilton-honors/guest/my-account/'),
            ('hilton',     '/en/hilton-honors/profile/awards'),
            ('hilton',     '/en/hilton-honors/profile/benefits'),
            ('hyatt',      '/en-US/my-account/awards'),
            ('united',        '/en/us/myaccount/awards'),
            ('alaska_air',    '/account/wallet'),
            # PA Utilities (utilities.cityofpaloalto.org)
            ('pa_utilities',  '/Account/'),
            ('pa_utilities',  '/Account/Overview'),
            ('pa_utilities',  '/Billing/'),
            ('pa_utilities',  '/Billing/Overview'),
            ('pa_utilities',  '/Usage/'),
        ]
        for _site, _path in _KNOWN_PATHS:
            try:
                db.execute(
                    "INSERT OR IGNORE INTO site_paths (site, path, reporter_count, last_seen, quality_score) "
                    "VALUES (?, ?, 5, datetime('now'), 5.0)",
                    (_site, _path)
                )
            except Exception:
                pass
        try:
            db.execute("ALTER TABLE actions ADD COLUMN consequence_level TEXT DEFAULT 'routine'")
        except Exception:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN notify_email INTEGER DEFAULT 1")
        except Exception:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN notify_ntfy INTEGER DEFAULT 1")
        except Exception:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN notify_push INTEGER DEFAULT 1")
        except Exception:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN onboarded INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN minimal_logging INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE account_data ADD COLUMN sync_failure_reason TEXT")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE account_data ADD COLUMN sync_status TEXT")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE account_data ADD COLUMN entry_url TEXT")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE account_credentials ADD COLUMN review_required_fields TEXT DEFAULT '[]'")
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN delete_raw_after_extract INTEGER DEFAULT 0")
        except Exception:
            pass  # may already exist
        try:
            db.execute("ALTER TABLE site_paths ADD COLUMN failure_count INTEGER DEFAULT 0")
        except Exception:
            pass
        try:
            # Remove legacy United /ual/ paths that cause 404s — current paths are /en/us/myaccount/*
            db.execute("DELETE FROM site_paths WHERE site='united' AND path LIKE '/ual/%'")
            db.commit()
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN notification_pref TEXT NOT NULL DEFAULT 'quiet'")
            db.commit()
        except Exception:
            pass  # column already exists
        try:
            db.execute("ALTER TABLE users ADD COLUMN preferred_name TEXT")
            db.commit()
        except Exception:
            pass  # column already exists
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS benefit_feedback (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      TEXT NOT NULL,
                    source       TEXT NOT NULL,
                    field_key    TEXT NOT NULL,
                    feedback     TEXT NOT NULL,
                    context      TEXT,
                    created_at   TEXT NOT NULL,
                    UNIQUE(user_id, source, field_key, context)
                )
            """)
            db.commit()
        except Exception:
            pass
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS notifications_sent (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       TEXT NOT NULL,
                    benefit_key   TEXT NOT NULL,
                    threshold_days INTEGER NOT NULL,
                    sent_at       TEXT NOT NULL,
                    UNIQUE(user_id, benefit_key, threshold_days)
                )
            """)
            db.commit()
        except Exception:
            pass
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS archived_benefits (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id     TEXT NOT NULL,
                    source      TEXT NOT NULL,
                    label       TEXT NOT NULL,
                    archived_at TEXT NOT NULL,
                    UNIQUE(user_id, source, label)
                )
            """)
            db.commit()
        except Exception:
            pass
        try:
            db.execute("ALTER TABLE users ADD COLUMN alert_expiry_emails INTEGER DEFAULT 1")
            db.commit()
        except Exception:
            pass  # column already exists
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS action_items (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       TEXT NOT NULL,
                    source        TEXT NOT NULL,
                    field_key     TEXT NOT NULL,
                    label         TEXT NOT NULL,
                    value         TEXT NOT NULL,
                    btype         TEXT NOT NULL,
                    urgency       TEXT NOT NULL,
                    days_left     INTEGER,
                    exp_date      TEXT,
                    created_at    TEXT NOT NULL,
                    dismissed_at  TEXT,
                    snoozed_until TEXT,
                    completed_at  TEXT,
                    UNIQUE(user_id, field_key)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_ai_user ON action_items(user_id, dismissed_at)")
            db.commit()
        except Exception:
            pass
        # Migration: add completed_at if table already existed without it
        try:
            db.execute("ALTER TABLE action_items ADD COLUMN completed_at TEXT")
            db.commit()
        except Exception:
            pass  # column already exists
        # Migration: intent aggregation summary on users
        try:
            db.execute("ALTER TABLE users ADD COLUMN intent_summary TEXT")
            db.commit()
        except Exception:
            pass  # column already exists
        # Benefit type corrections — user-supplied overrides for misclassified items
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS benefit_corrections (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id        TEXT NOT NULL,
                    field_key      TEXT NOT NULL,
                    corrected_type TEXT NOT NULL,
                    corrected_at   TEXT NOT NULL,
                    UNIQUE(user_id, field_key)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_bc_user ON benefit_corrections(user_id)")
            db.commit()
        except Exception:
            pass
        # Per-user learned type affinity — updated on every interaction signal
        try:
            db.execute("ALTER TABLE users ADD COLUMN type_affinity TEXT")
            db.commit()
        except Exception:
            pass  # column already exists
        # Email scan — connected providers (tokens stored encrypted)
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS email_connections (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       TEXT NOT NULL,
                    provider      TEXT NOT NULL,
                    access_token_enc  TEXT,
                    refresh_token_enc TEXT,
                    email_address TEXT,
                    scanned_at    TEXT,
                    created_at    TEXT NOT NULL,
                    UNIQUE(user_id, provider)
                )
            """)
            db.commit()
        except Exception:
            pass
        # Email scan — surfaced account suggestions per user
        try:
            db.execute("""
                CREATE TABLE IF NOT EXISTS email_suggestions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       TEXT NOT NULL,
                    site_key      TEXT NOT NULL,
                    display_name  TEXT NOT NULL,
                    category      TEXT NOT NULL,
                    email_count   INTEGER DEFAULT 0,
                    sender_domain TEXT,
                    dismissed     INTEGER DEFAULT 0,
                    added         INTEGER DEFAULT 0,
                    created_at    TEXT NOT NULL,
                    UNIQUE(user_id, site_key)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_esugg_user ON email_suggestions(user_id, dismissed)")
            db.commit()
        except Exception:
            pass

init_db()
print(f"[Mighty] POSTMARK_API_KEY={'set' if POSTMARK_API_KEY else 'NOT SET'}", flush=True)


# ── Expiry alert helpers ───────────────────────────────────────────────────────

def _parse_expiry_days(label, val):
    """Return days until expiry from a label+value string, or None."""
    import re as _re_exp
    import datetime as _dt_exp
    combined = f"{label} {val}"
    _mm_map = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
               'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
    # MM/DD/YYYY
    m = _re_exp.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', combined)
    if m:
        try:
            mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if yr < 100: yr += 2000
            return (_dt_exp.date(yr, mo, da) - _dt_exp.date.today()).days
        except Exception: pass
    # "Aug 31, 2026" or "August 31, 2026" (day present)
    m3 = _re_exp.search(
        r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{1,2}),?\s+(\d{4})',
        combined, _re_exp.I)
    if m3:
        try:
            mo = _mm_map[m3.group(1)[:3].lower()]
            da = int(m3.group(2))
            yr = int(m3.group(3))
            return (_dt_exp.date(yr, mo, da) - _dt_exp.date.today()).days
        except Exception: pass
    # "Aug 2026" (no day — use 1st of month)
    m2 = _re_exp.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{4})',
                         combined, _re_exp.I)
    if m2:
        try:
            mo = _mm_map[m2.group(1)[:3].lower()]
            yr = int(m2.group(2))
            return (_dt_exp.date(yr, mo, 1) - _dt_exp.date.today()).days
        except Exception: pass
    return None

_ALERT_THRESHOLDS = [7, 14, 30]  # send at each crossing, closest first

# ── Action Center persistence ──────────────────────────────────────────────────

_ENTRY_THRESHOLD = 30  # minimum score for an action item to surface

def populate_action_items(uid: str, source: str, data: dict):
    """Upsert action_items rows from freshly-synced data for one source.

    Called immediately after account_data is written so items are persistent and
    dismiss/snooze state survives re-syncs.  The UNIQUE constraint on
    (user_id, field_key) means ON CONFLICT updates the content columns while
    preserving dismissed_at / snoozed_until set by the user.

    Urgency is now driven by score_opportunity() — a composite of expiration,
    literal value, user intent, and type rarity — so high-value items (e.g. a
    $600 flight credit with no expiry) surface even without time pressure.
    """
    try:
        from mighty.classify import classify_benefit, is_actionable, is_needs_attention
        from mighty.scoring import score_opportunity, urgency_from_score, urgency_for_attention
    except Exception:
        return

    import datetime as _dt_ai
    import json as _json_ai

    items = (data.get("items") or []) + (data.get("ai_items") or [])
    now   = _dt_ai.datetime.utcnow().isoformat()
    today = _dt_ai.date.today()

    # Load user's intent summary for scoring (non-fatal if missing)
    user_intent: dict = {}
    user_type_affinity: dict = {}
    try:
        _ui_row = get_db().execute(
            "SELECT intent_summary, type_affinity FROM users WHERE id=?", (uid,)
        ).fetchone()
        if _ui_row and _ui_row["intent_summary"]:
            user_intent = _json_ai.loads(_ui_row["intent_summary"])
        if _ui_row and _ui_row["type_affinity"]:
            user_type_affinity = _json_ai.loads(_ui_row["type_affinity"])
    except Exception:
        pass

    # Load user corrections: {field_key → corrected_type}
    corrections = _get_user_corrections(uid)

    # Action center entry threshold: score ≥ 30 (filters pure noise)

    # Collect field_keys we'll upsert so we can cull stale rows afterward
    live_keys: list[str] = []

    db = get_db()
    for item in items:
        label = item.get("label", "").strip()
        value = str(item.get("value", "")).strip()
        if not label or not value or value in {"—", "-", "N/A", "None", "TBD", "0"}:
            continue

        field_key = f"{source}::{label}"

        # Priority: user correction > stored _type > on-the-fly classify
        if field_key in corrections:
            btype = corrections[field_key]
        else:
            btype = item.get("_type") or classify_benefit(label, value, source)

        # Only persist actionable or attention types
        if not (is_actionable(btype) or is_needs_attention(btype)):
            continue

        days_left = _parse_expiry_days(label, value)
        exp_date  = None
        if days_left is not None and days_left >= 0:
            exp_date = (today + _dt_ai.timedelta(days=days_left)).isoformat()

        # Attention types (payment_due, renewal) use time-based urgency — their
        # urgency IS the time pressure, not composite value/intent.
        # Actionable types use the composite score engine.
        if is_needs_attention(btype):
            should_add = True
            urgency    = urgency_for_attention(days_left)
        else:
            _score_item = {"label": label, "value": value, "btype": btype, "days_left": days_left}
            score   = score_opportunity(_score_item, user_intent=user_intent, source=source,
                                        user_type_affinity=user_type_affinity)
            urgency = urgency_from_score(score)
            should_add = score >= _ENTRY_THRESHOLD

        if not should_add:
            continue

        live_keys.append(field_key)

        db.execute(
            """
            INSERT INTO action_items
                (user_id, source, field_key, label, value, btype, urgency, days_left,
                 exp_date, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(user_id, field_key) DO UPDATE SET
                label       = excluded.label,
                value       = excluded.value,
                btype       = excluded.btype,
                urgency     = excluded.urgency,
                days_left   = excluded.days_left,
                exp_date    = excluded.exp_date
            """,
            (uid, source, field_key, label, value, btype, urgency, days_left,
             exp_date, now)
        )

    # Remove stale rows for this source that no longer appear in the data.
    # Preserve completed rows — they're training signal even if the field disappeared.
    if live_keys:
        placeholders = ",".join("?" * len(live_keys))
        db.execute(
            f"DELETE FROM action_items WHERE user_id=? AND source=? AND field_key NOT IN ({placeholders})"
            f" AND completed_at IS NULL",
            [uid, source] + live_keys
        )
    else:
        db.execute(
            "DELETE FROM action_items WHERE user_id=? AND source=? AND completed_at IS NULL",
            (uid, source)
        )

    db.commit()


def _open_action_items(uid: str) -> list[dict]:
    """Return open (not dismissed, not snoozed, not completed) action items for uid."""
    import datetime as _dt_ai2
    now = _dt_ai2.datetime.utcnow().isoformat()
    rows = get_db().execute(
        """
        SELECT id, source, label, value, btype, urgency, days_left, exp_date
        FROM action_items
        WHERE user_id = ?
          AND dismissed_at IS NULL
          AND completed_at IS NULL
          AND (snoozed_until IS NULL OR snoozed_until < ?)
        ORDER BY
            CASE urgency WHEN 'urgent' THEN 0 WHEN 'soon' THEN 1 ELSE 2 END,
            CASE WHEN days_left IS NULL THEN 9999 ELSE days_left END
        """,
        (uid, now)
    ).fetchall()
    return [dict(r) for r in rows]


def _get_user_corrections(uid: str) -> dict[str, str]:
    """Return a dict of {field_key: corrected_type} for a user.

    Loaded once per request and used to override classifier output on any
    surface that reads _type — hero bullets, top benefits, action center.
    Returns empty dict on any error so callers can treat it as optional.
    """
    try:
        rows = get_db().execute(
            "SELECT field_key, corrected_type FROM benefit_corrections WHERE user_id=?",
            (uid,)
        ).fetchall()
        return {r["field_key"]: r["corrected_type"] for r in rows}
    except Exception:
        return {}


def _recompute_intent_summary(uid: str) -> dict:
    """Aggregate intent_history rows from the last 30 days into a count dict.

    Writes the result back to users.intent_summary so the Opportunity Engine
    can use it for relevance ranking without re-querying intent_history every
    page load.

    Returns the summary dict for the caller.
    """
    import json as _json_is
    import datetime as _dt_is
    cutoff = (_dt_is.datetime.utcnow() - _dt_is.timedelta(days=30)).isoformat()
    db = get_db()
    rows = db.execute(
        "SELECT intent_type, COUNT(*) AS cnt FROM intent_history "
        "WHERE user_id=? AND detected_at > ? GROUP BY intent_type",
        (uid, cutoff)
    ).fetchall()
    summary = {r["intent_type"]: r["cnt"] for r in rows}
    summary["_computed_at"] = _dt_is.datetime.utcnow().isoformat()
    db.execute(
        "UPDATE users SET intent_summary=? WHERE id=?",
        (_json_is.dumps(summary), uid)
    )
    db.commit()
    return summary


def _recompute_type_affinity(uid: str) -> dict:
    """Aggregate interaction signals into a per-btype affinity score.

    Signal weights (per item interaction):
        completed           → +3.0  (user redeemed / acted — strongest positive)
        feedback: useful    → +2.0
        feedback: already_used → +1.5
        snoozed (no dismiss/complete) → -0.5  (mild avoidance)
        dismissed           → -1.0
        feedback: not_relevant → -2.0
        feedback: dont_show → -3.0

    Affinity is accumulated per canonical benefit type (btype).  The resulting
    dict is stored as JSON on users.type_affinity and used by score_opportunity
    to add up to 15 bonus points for types the user consistently acts on.

    Returns the computed dict (without _computed_at — kept lean for scoring).
    """
    import json as _json_ta
    import datetime as _dt_ta

    db = get_db()

    # Aggregate action_items signals (last 90 days of rows, including preserved completed ones)
    cutoff = (_dt_ta.datetime.utcnow() - _dt_ta.timedelta(days=90)).isoformat()

    ai_rows = db.execute(
        """
        SELECT btype,
               SUM(CASE WHEN completed_at IS NOT NULL THEN 1 ELSE 0 END) AS completed,
               SUM(CASE WHEN dismissed_at IS NOT NULL
                         AND completed_at IS NULL THEN 1 ELSE 0 END)    AS dismissed,
               SUM(CASE WHEN snoozed_until IS NOT NULL
                         AND dismissed_at IS NULL
                         AND completed_at IS NULL THEN 1 ELSE 0 END)    AS snoozed_only
        FROM action_items
        WHERE user_id = ? AND created_at > ?
        GROUP BY btype
        """,
        (uid, cutoff)
    ).fetchall()

    # Aggregate benefit_feedback signals
    fb_rows = db.execute(
        """
        SELECT ai.btype,
               bf.feedback,
               COUNT(*) AS cnt
        FROM benefit_feedback bf
        JOIN action_items ai ON ai.user_id = bf.user_id
                             AND ai.source = bf.source
                             AND ai.field_key = (bf.source || '::' || ai.label)
        WHERE bf.user_id = ? AND bf.created_at > ?
        GROUP BY ai.btype, bf.feedback
        """,
        (uid, cutoff)
    ).fetchall()

    affinity: dict[str, float] = {}

    for r in ai_rows:
        bt = r["btype"]
        score = (r["completed"] or 0) * 3.0 \
              - (r["dismissed"] or 0) * 1.0 \
              - (r["snoozed_only"] or 0) * 0.5
        affinity[bt] = affinity.get(bt, 0.0) + score

    _FB_WEIGHTS = {
        "useful":        2.0,
        "already_used":  1.5,
        "not_relevant": -2.0,
        "dont_show":    -3.0,
    }
    for r in fb_rows:
        bt = r["btype"]
        w  = _FB_WEIGHTS.get(r["feedback"], 0.0)
        affinity[bt] = affinity.get(bt, 0.0) + w * (r["cnt"] or 0)

    affinity["_computed_at"] = _dt_ta.datetime.utcnow().isoformat()

    db.execute(
        "UPDATE users SET type_affinity=? WHERE id=?",
        (_json_ta.dumps(affinity), uid)
    )
    db.commit()
    return affinity


def _relevant_now_items(uid: str, max_items: int = 3) -> list[dict]:
    """Return benefits contextually relevant to the user's most recent intent signal.

    Reads the latest intent_history row within the last 48 hours.  Maps the
    intent domain to adjacent domains (flight → hotel, car; hotel → flight, car;
    etc.) then walks the user's account_data to find actionable/certificate
    benefits from those adjacent sources.  Items are scored and the top
    max_items returned.

    Returns [] when there is no fresh intent signal.

    Each returned dict has:
        source, label, value, btype, days_left, score, why, intent_type
    """
    import datetime as _dt_rn
    import json as _json_rn

    _ADJACENT: dict[str, list[str]] = {
        "flight":      ["hotel", "car"],
        "hotel":       ["flight", "car"],
        "car":         ["flight", "hotel"],
        "credit_card": ["flight", "hotel", "car", "shopping", "dining"],
        "shopping":    ["dining"],
        "dining":      ["shopping"],
    }

    # Source key → intent domain (mirrors scoring._SOURCE_DOMAIN)
    _SRC_DOMAIN: dict[str, str] = {
        "delta": "flight", "american": "flight", "united": "flight",
        "southwest": "flight", "alaska": "flight", "jetblue": "flight",
        "spirit": "flight", "frontier": "flight", "hawaiian": "flight",
        "hyatt": "hotel", "marriott": "hotel", "hilton": "hotel",
        "ihg": "hotel", "wyndham": "hotel", "accor": "hotel",
        "hertz": "car", "avis": "car", "enterprise": "car",
        "national": "car", "budget": "car", "alamo": "car", "thrifty": "car",
        "amazon": "shopping", "walmart": "shopping", "target": "shopping",
        "doordash": "dining", "ubereats": "dining", "grubhub": "dining",
        "amex": "credit_card", "chase": "credit_card", "citi": "credit_card",
        "capital one": "credit_card", "discover": "credit_card",
    }

    def _src_to_domain(source: str) -> str | None:
        s = source.lower().replace("_", " ")
        for frag, dom in _SRC_DOMAIN.items():
            if frag in s:
                return dom
        return None

    try:
        from mighty.classify import classify_benefit, is_actionable, BENEFIT_TYPES
        from mighty.scoring import score_opportunity
    except Exception:
        return []

    db = get_db()
    cutoff_48h = (_dt_rn.datetime.utcnow() - _dt_rn.timedelta(hours=48)).isoformat()

    # Most recent intent signal within 48 hours
    recent = db.execute(
        "SELECT intent_type, page_url, detected_at FROM intent_history "
        "WHERE user_id=? AND detected_at > ? ORDER BY detected_at DESC LIMIT 1",
        (uid, cutoff_48h)
    ).fetchone()
    if not recent:
        return []

    intent_type = recent["intent_type"]
    adjacent_domains = _ADJACENT.get(intent_type, [])
    if not adjacent_domains:
        return []

    # Load user's corrections (field_key → type)
    corrections = _get_user_corrections(uid)

    # Load user intent summary and type affinity for scoring
    user_intent: dict = {}
    user_type_affinity: dict = {}
    try:
        _ui_r = db.execute(
            "SELECT intent_summary, type_affinity FROM users WHERE id=?", (uid,)
        ).fetchone()
        if _ui_r and _ui_r["intent_summary"]:
            user_intent = _json_rn.loads(_ui_r["intent_summary"])
        if _ui_r and _ui_r["type_affinity"]:
            user_type_affinity = _json_rn.loads(_ui_r["type_affinity"])
    except Exception:
        pass

    # Walk all synced accounts
    accounts = db.execute(
        "SELECT source, display_name, data_enc, synced_at FROM account_data WHERE user_id=?",
        (uid,)
    ).fetchall()

    candidates: list[dict] = []
    seen_keys: set[str] = set()

    for acct in accounts:
        source = acct["source"]
        src_domain = _src_to_domain(source)
        if src_domain not in adjacent_domains:
            continue  # not an adjacent domain — skip

        if not acct["data_enc"]:
            continue
        try:
            data = decrypt_account_data(uid, acct["data_enc"])
            if not isinstance(data, dict):
                continue
        except Exception:
            continue

        items = (data.get("items") or []) + (data.get("ai_items") or [])
        for item in items:
            label = (item.get("label") or "").strip()
            value = str(item.get("value") or "").strip()
            if not label or not value or value in {"—", "-", "N/A", "None", "TBD", "0"}:
                continue

            field_key = f"{source}::{label}"
            if field_key in seen_keys:
                continue

            # Resolve type: correction > stored > classify
            if field_key in corrections:
                btype = corrections[field_key]
            else:
                btype = item.get("_type") or classify_benefit(label, value, source)

            if not is_actionable(btype):
                continue  # only surface redeemable benefits

            days_left = _parse_expiry_days(label, value)
            if days_left is not None and days_left < 0:
                continue  # expired

            score_item = {"label": label, "value": value, "btype": btype, "days_left": days_left}
            score = score_opportunity(score_item, user_intent=user_intent, source=source,
                                      user_type_affinity=user_type_affinity)
            if score < 20:
                continue  # too low signal even in context

            seen_keys.add(field_key)
            candidates.append({
                "source":      acct["display_name"] or source,
                "source_key":  source,
                "field_key":   field_key,
                "label":       label,
                "value":       value,
                "btype":       btype,
                "days_left":   days_left,
                "score":       score,
                "intent_type": intent_type,
            })

    if not candidates:
        return []

    candidates.sort(key=lambda x: -x["score"])
    return candidates[:max_items]


def _why_shown(btype: str, days_left=None, source_display: str = "",
               methodology: str = "") -> str:
    """Return a one-sentence 'Why am I seeing this?' explanation for a benefit item.

    Used in hero bullets, top benefits, wallet insights, and action center.
    Kept intentionally short — max ~60 chars, no leading capital, no trailing period.
    """
    d = days_left  # alias for brevity
    if btype == "certificate":
        if d is not None and d <= 14:  return f"expires in {d} day{'s' if d != 1 else ''} — use it soon"
        if d is not None and d <= 60:  return f"expires in {d} days"
        return "unused reward in your account"
    if btype == "travel_credit":
        if d is not None and d <= 30:  return f"credit expires in {d} days"
        return "unused travel credit"
    if btype == "cash_credit":
        if d is not None and d <= 30:  return f"credit expires in {d} days"
        return "available statement credit"
    if btype == "elite_status":
        src = f" with {source_display}" if source_display else ""
        return f"your current status{src}"
    if btype == "points_balance":
        src = f" in {source_display}" if source_display else ""
        return f"redeemable balance{src}"
    if btype == "progress_toward":
        return "tracking toward your next tier"
    if btype == "membership":
        return "active membership benefit"
    if btype == "payment_due":
        if d is not None and d == 0:   return "due today"
        if d is not None and d <= 3:   return f"due in {d} day{'s' if d != 1 else ''}"
        if d is not None:              return f"due in {d} days"
        return "upcoming payment"
    if btype == "renewal":
        if d is not None:              return f"renews in {d} days"
        return "upcoming renewal"
    if btype == "upcoming_event":
        if d is not None and d == 0:   return "today"
        if d is not None and d == 1:   return "tomorrow"
        if d is not None:              return f"in {d} days"
        return "upcoming commitment"
    if btype == "partner_benefit":
        if methodology:                return methodology[:80]
        if source_display:             return f"unlocked via {source_display}"
        return "derived from your existing benefits"
    if btype == "reservation":
        return "confirmed booking"
    return ""  # "other" — omit rather than show a generic line


def _find_expiring_for_user(uid):
    """Return list of expiring benefit dicts for a user. Only certs+credits within 30d."""
    db = get_db()
    rows = db.execute(
        "SELECT source, display_name, data_enc FROM account_data WHERE user_id=?",
        (uid,)
    ).fetchall()
    results = []
    for row in rows:
        display = row["display_name"] or row["source"]
        try:
            data = decrypt_account_data(uid, row["data_enc"] or "")
        except Exception:
            continue
        # Items are stored under "items" (scraped) and "ai_items" (AI-extracted)
        all_items = []
        for key in ("items", "ai_items"):
            chunk = data.get(key)
            if isinstance(chunk, list):
                all_items.extend(chunk)
        for item in all_items:
            if not isinstance(item, dict):
                continue
            lbl = item.get("label", "")
            val = str(item.get("value", "")).strip()
            if not val or val.lower() in {"0", "—", "-", "n/a", "none", "tbd", ""}:
                continue
            btype = classify_benefit(lbl, val)
            if btype not in ("certificate", "travel_credit", "cash_credit"):
                continue
            exp = _parse_expiry_days(lbl, val)
            if exp is None or exp < 0 or exp > 30:
                continue
            field_key = item.get("field_key") or item.get("key") or lbl[:40]
            results.append({
                "source": row["source"],
                "display": display,
                "label": lbl,
                "value": val,
                "exp_days": exp,
                "field_key": field_key,
                "benefit_key": f"{row['source']}:{field_key}",
            })
    # De-duplicate by benefit_key, keep shortest exp_days
    seen = {}
    for r in results:
        k = r["benefit_key"]
        if k not in seen or r["exp_days"] < seen[k]["exp_days"]:
            seen[k] = r
    return list(seen.values())

def _build_expiry_email_html(name, items, base_url, user_id):
    """Build HTML for expiry alert email."""
    import html as _html_mod
    greeting = f"Hi {name}," if name else "Hi,"
    rows_html = ""
    for it in items:
        exp = it["exp_days"]
        if exp <= 7:
            badge_bg, badge_color, badge_txt = "#fef2f2", "#dc2626", f"Expires in {exp} day{'s' if exp!=1 else ''}"
        elif exp <= 14:
            badge_bg, badge_color, badge_txt = "#fffbeb", "#d97706", f"Expires in {exp} days"
        else:
            badge_bg, badge_color, badge_txt = "#f0fdf4", "#059669", f"Expires in {exp} days"

        lbl_e = _html_mod.escape(it["label"])
        val_e = _html_mod.escape(it["value"])
        disp_e = _html_mod.escape(it["display"])
        rows_html += f"""
        <div style="border:1px solid #e5e7eb;border-radius:10px;padding:14px 16px;margin-bottom:12px;background:#fff">
          <div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px">
            <div>
              <div style="font-size:14px;font-weight:600;color:#111;line-height:1.35">{lbl_e}</div>
              <div style="font-size:12px;color:#6b7280;margin-top:2px">{disp_e} · {val_e}</div>
            </div>
            <div style="flex-shrink:0;background:{badge_bg};color:{badge_color};
                        font-size:11px;font-weight:700;border-radius:20px;
                        padding:3px 10px;white-space:nowrap;margin-top:2px">{badge_txt}</div>
          </div>
        </div>"""

    settings_url = f"{base_url}/settings"
    dashboard_url = f"{base_url}/dashboard"
    count = len(items)
    subject_preview = f"{count} benefit{'s' if count!=1 else ''} expiring soon"

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f8f7f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif">
  <div style="max-width:480px;margin:40px auto;padding:0 16px">
    <div style="margin-bottom:20px">
      <span style="font-size:18px;font-weight:700;color:#1a1a1a">⚡ Mighty</span>
    </div>
    <div style="background:#fff;border:1px solid #e5e3df;border-radius:16px;overflow:hidden">
      <div style="background:#fff7ed;border-bottom:1px solid #fed7aa;padding:12px 20px">
        <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#c2410c">Use it or lose it</span>
      </div>
      <div style="padding:20px">
        <div style="font-size:16px;font-weight:700;color:#1a1a1a;margin-bottom:4px">{greeting}</div>
        <div style="font-size:14px;color:#6b7280;margin-bottom:20px;line-height:1.5">
          You have <strong style="color:#1a1a1a">{subject_preview}</strong>. Here's what to use before time runs out:
        </div>
        {rows_html}
        <a href="{dashboard_url}" style="display:block;padding:13px;background:#111;color:#fff;
           text-decoration:none;border-radius:10px;font-size:14px;font-weight:700;
           text-align:center;margin-top:8px">View in Mighty →</a>
      </div>
    </div>
    <div style="text-align:center;margin-top:16px;font-size:11px;color:#9ca3af">
      <a href="{settings_url}" style="color:#9ca3af">Turn off expiry emails in Settings</a>
    </div>
  </div>
</body>
</html>"""

def send_expiry_alert_email(to_email, name, items, base_url, user_id):
    """Send expiry alert via Postmark. Non-blocking."""
    if not POSTMARK_API_KEY:
        print("[Alerts] Skipped — POSTMARK_API_KEY not set", flush=True)
        return
    count = len(items)
    if count == 1:
        first = items[0]
        subject = f"Your {first['label']} expires in {first['exp_days']} days — {first['display']}"
    else:
        subject = f"{count} benefits expiring soon — time to use them"

    html = _build_expiry_email_html(name, items, base_url, user_id)
    payload = json.dumps({
        "From":      POSTMARK_FROM,
        "To":        to_email,
        "Subject":   subject,
        "HtmlBody":  html,
        "Tag":       "expiry-alert",
        "TrackOpens": False,
    }).encode()

    def _send():
        try:
            req = urllib.request.Request(
                "https://api.postmarkapp.com/email",
                data=payload,
                headers={
                    "X-Postmark-Server-Token": POSTMARK_API_KEY,
                    "Content-Type":            "application/json",
                    "Accept":                  "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                _masked_email = to_email[:2] + "***@" + to_email.split("@")[-1] if "@" in to_email else "***"
                print(f"[Alerts] Sent expiry email to {_masked_email}: {resp.status}", flush=True)
        except Exception as e:
            print(f"[Alerts] Email send failed: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()

def run_expiry_alerts(dry_run=False):
    """Check all opted-in users for expiring benefits and send alerts as needed."""
    import datetime as _dt_alert
    db = get_db()
    users = db.execute(
        "SELECT id, email, preferred_name, alert_expiry_emails FROM users"
    ).fetchall()
    base_url = os.environ.get("BASE_URL", "https://mighty-selfserve-production.up.railway.app")
    sent_count = 0
    for user in users:
        if not user["alert_expiry_emails"]:
            continue
        uid      = user["id"]
        to_email = user["email"]
        name     = user["preferred_name"] or ""
        items    = _find_expiring_for_user(uid)
        if not items:
            continue
        # Decide which threshold each item crosses today
        to_send = []
        for item in items:
            exp = item["exp_days"]
            # Find the tightest threshold this item is within
            threshold = None
            for t in sorted(_ALERT_THRESHOLDS):  # [7, 14, 30]
                if exp <= t:
                    threshold = t
                    break
            if threshold is None:
                continue
            bkey = item["benefit_key"]
            already = db.execute(
                "SELECT 1 FROM notifications_sent WHERE user_id=? AND benefit_key=? AND threshold_days=?",
                (uid, bkey, threshold)
            ).fetchone()
            if already:
                continue
            to_send.append((item, threshold))

        if not to_send:
            continue

        if dry_run:
            print(f"[Alerts][DRY] Would send to {to_email}: {[i['label'] for i,_ in to_send]}", flush=True)
            continue

        send_expiry_alert_email(to_email, name, [i for i, _ in to_send], base_url, uid)
        sent_count += 1

        # Record sent notifications
        now = _dt_alert.datetime.utcnow().isoformat()
        for item, threshold in to_send:
            try:
                db.execute(
                    "INSERT OR IGNORE INTO notifications_sent "
                    "(user_id, benefit_key, threshold_days, sent_at) VALUES (?,?,?,?)",
                    (uid, item["benefit_key"], threshold, now)
                )
            except Exception:
                pass
        db.commit()

    print(f"[Alerts] run_expiry_alerts complete — {sent_count} email(s) sent", flush=True)
    return sent_count

def _start_alert_scheduler():
    """Run expiry alerts daily at 08:00 UTC.

    NOTE: This in-process scheduler is fine for a single Railway instance.
    If you scale to multiple instances, move this to a Railway cron job calling
    POST /api/internal/trigger-alerts instead, to avoid duplicate sends.
    """
    import time as _time_sched
    import datetime as _dt_sched
    def _loop():
        _time_sched.sleep(60)  # short startup delay
        while True:
            now = _dt_sched.datetime.utcnow()
            # Compute seconds until next 08:00 UTC
            target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= target:
                target += _dt_sched.timedelta(days=1)
            wait = (target - now).total_seconds()
            print(f"[Alerts] Next expiry check at {target.isoformat()}Z ({wait/3600:.1f}h away)", flush=True)
            _time_sched.sleep(wait)
            try:
                # get_db() requires Flask app context outside a request
                with app.app_context():
                    run_expiry_alerts()
            except Exception as e:
                print(f"[Alerts] run_expiry_alerts error: {e}", flush=True)
    t = threading.Thread(target=_loop, daemon=True)
    t.start()
    print("[Mighty] Expiry alert scheduler started (daily at 08:00 UTC)", flush=True)


# ── VAPID key management (push notifications — optional) ─────────────────────

def get_vapid_keys():
    """Return (private_key_base64url, public_key_base64url) or (None, None) if py_vapid unavailable."""
    try:
        import base64
        from py_vapid import Vapid
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    except ImportError:
        return None, None

    def _generate(db):
        v = Vapid()
        v.generate_keys()
        # Raw 32-byte EC private scalar → base64url (what pywebpush 2.x expects)
        priv_int   = v.private_key.private_numbers().private_value
        priv       = base64.urlsafe_b64encode(priv_int.to_bytes(32, 'big')).rstrip(b'=').decode()
        # Uncompressed EC point → base64url (what browsers expect as applicationServerKey)
        pub_bytes  = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        pub        = base64.urlsafe_b64encode(pub_bytes).rstrip(b'=').decode()
        db.execute("INSERT OR REPLACE INTO settings VALUES ('vapid_private', ?)", (priv,))
        db.execute("INSERT OR REPLACE INTO settings VALUES ('vapid_public',  ?)", (pub,))
        db.commit()
        return priv, pub

    try:
        with sqlite3.connect(DATABASE) as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            row = db.execute("SELECT value FROM settings WHERE key='vapid_private'").fetchone()
            if row and not row[0].startswith('-----BEGIN'):
                # Already stored in correct raw base64url format
                priv = row[0]
                pub  = db.execute("SELECT value FROM settings WHERE key='vapid_public'").fetchone()[0]
                return priv, pub
            # First run, or old PEM format — generate fresh keys
            return _generate(db)
    except Exception as _ve:
        print(f"[Mighty] VAPID key init failed: {_ve} — push notifications disabled", flush=True)
        return None, None

VAPID_PRIVATE, VAPID_PUBLIC = get_vapid_keys()
if VAPID_PUBLIC:
    print(f"[Mighty] VAPID public key: {VAPID_PUBLIC[:20]}...", flush=True)
else:
    print("[Mighty] Push notifications disabled (py_vapid not available)", flush=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def hash_pw(pw):
    return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt(rounds=12)).decode()

def check_pw(stored, provided):
    # Support legacy SHA-256 format "hexsalt:hexhash" for existing accounts
    if stored and ":" in stored and not stored.startswith("$2"):
        try:
            salt, h = stored.split(":", 1)
            return hashlib.sha256(f"{salt}{provided}".encode()).hexdigest() == h
        except Exception:
            return False
    try:
        return _bcrypt.checkpw(provided.encode(), stored.encode())
    except Exception:
        return False

def utcnow():
    return datetime.now(timezone.utc)

def _fmt_sync(ts):
    """Format a UTC ISO timestamp as a human-readable relative time string."""
    try:
        clean = ts.replace('Z', '+00:00') if ts and ts.endswith('Z') else ts
        dt = datetime.fromisoformat(clean)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        secs = int((utcnow() - dt).total_seconds())
        if secs < 60:    return "just now"
        mins = secs // 60
        if mins < 2:     return "just now"
        if mins < 60:    return f"{mins} minute{'s' if mins != 1 else ''} ago"
        hrs = mins // 60
        if hrs < 24:    return f"{hrs} hour{'s' if hrs != 1 else ''} ago"
        days = hrs // 24
        return f"{days} day{'s' if days != 1 else ''} ago"
    except Exception:
        return ts[:10] if ts else "—"

def _freshness_label(synced_at: str | None, sync_status: str = "ok") -> tuple:
    """Return a (label, color, icon) tuple for display on cards.

    Stale (3+ days) and login-required states use red + bold to draw attention.
    Returns (label, color, icon) — callers that need font-weight should check
    whether the color is '#dc2626' and apply font-weight:600 accordingly.
    """
    if sync_status == "login_required":
        return ("🔐 Login required", "#dc2626", "")
    if sync_status == "no_data" or not synced_at:
        return ("No data", "#9ca3af", "—")
    try:
        import datetime as _dt
        age_h = (_dt.datetime.utcnow() - _dt.datetime.fromisoformat(
            synced_at.rstrip("Z"))).total_seconds() / 3600
        if age_h < 1:
            return ("Just now", "#22c55e", "✓")
        elif age_h < 2:
            return (f"{int(age_h*60)}m ago", "#22c55e", "✓")
        elif age_h < 24:
            return (f"{int(age_h)}h ago", "#6b7280", "✓")
        elif age_h < 48:
            return ("Yesterday", "#f59e0b", "~")
        elif age_h < 72:
            return (f"{int(age_h/24)}d ago", "#f59e0b", "~")
        else:
            return ("Stale", "#dc2626", "!")
    except Exception:
        return ("Unknown", "#9ca3af", "?")

def _log_privacy_event(uid: str, event_type: str, source: str = None, domain: str = None, detail: str = None):
    """Log a privacy-relevant event for the user's audit log."""
    try:
        get_db().execute(
            "INSERT INTO privacy_audit_log (user_id, event_type, source, domain, detail, created_at) VALUES (?,?,?,?,?,?)",
            (uid, event_type, source, domain, detail, iso())
        )
        get_db().commit()
    except Exception:
        pass


def _sidebar_html(active: str, email: str, csrf: str) -> str:
    """Generate the shared left sidebar HTML — icon-only, 48px."""
    def _nav(href, label, icon_svg, page_key):
        cls = "sidebar-link sidebar-link-active" if active == page_key else "sidebar-link"
        return f'<a href="{href}" class="{cls}">{icon_svg}<span class="sidebar-tip">{label}</span></a>'
    icon_dash = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>'
    icon_acct = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg>'
    icon_sett = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>'
    av = (email[0] if email else "?").upper()
    _nav_links = (
        _nav('/dashboard', 'Dashboard', icon_dash, 'dashboard')
        + _nav('/credentials', 'Accounts', icon_acct, 'accounts')
        + _nav('/settings', 'Settings', icon_sett, 'settings')
    )
    _logout_form = (
        f'<form method="POST" action="/logout" style="margin:0;display:flex;justify-content:center">'
        f'<input type="hidden" name="_csrf" value="{he(csrf)}">'
        f'<button class="sidebar-avatar" type="submit" title="Sign out" onclick="return confirm(\'Sign out of Mighty?\')">{av}<span class="sidebar-tip">Sign out</span></button>'
        f'</form>'
    )
    return (
        # Desktop sidebar
        '<aside class="sidebar" id="desktop-sidebar">'
        '<div class="sidebar-header">'
        '<a href="/dashboard" class="sidebar-logo">'
        '<img src="/logo-icon.png" alt="Mighty" class="sidebar-logo-img">'
        '<span class="sidebar-tip">Mighty</span>'
        '</a></div>'
        '<nav class="sidebar-nav">' + _nav_links + '</nav>'
        '<div class="sidebar-footer">' + _logout_form + '</div>'
        '</aside>'

        # Mobile drawer overlay — hidden by default
        '<div id="mobile-drawer-overlay" onclick="closeMobileDrawer()" '
        'style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.45);z-index:199"></div>'
        '<aside id="mobile-drawer" '
        'style="display:flex;flex-direction:column;position:fixed;top:0;left:0;width:220px;height:100vh;'
        'background:#0a0c12;z-index:200;padding:16px 12px;box-sizing:border-box;'
        'transform:translateX(-100%);transition:transform 0.25s cubic-bezier(.4,0,.2,1)">'
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px">'
        '<a href="/dashboard" style="display:flex;align-items:center;gap:8px;text-decoration:none">'
        '<img src="/logo-icon.png" alt="Mighty" style="width:28px;height:28px">'
        '<span style="font-size:15px;font-weight:700;color:#fff">Mighty</span>'
        '</a>'
        '<button onclick="closeMobileDrawer()" style="background:none;border:none;color:#9ca3af;'
        'cursor:pointer;font-size:20px;line-height:1;padding:4px">✕</button>'
        '</div>'
        '<nav style="display:flex;flex-direction:column;gap:4px">'
        '<a href="/dashboard" style="display:flex;align-items:center;gap:10px;padding:10px 12px;'
        'border-radius:8px;text-decoration:none;color:#d1d5db;font-size:14px;font-weight:500">'
        + icon_dash + ' Dashboard</a>'
        '<a href="/credentials" style="display:flex;align-items:center;gap:10px;padding:10px 12px;'
        'border-radius:8px;text-decoration:none;color:#d1d5db;font-size:14px;font-weight:500">'
        + icon_acct + ' Accounts</a>'
        '<a href="/settings" style="display:flex;align-items:center;gap:10px;padding:10px 12px;'
        'border-radius:8px;text-decoration:none;color:#d1d5db;font-size:14px;font-weight:500">'
        + icon_sett + ' Settings</a>'
        '</nav>'
        '<div style="margin-top:auto;padding-top:16px;border-top:1px solid rgba(255,255,255,0.08)">'
        + _logout_form.replace('justify-content:center', 'justify-content:flex-start') +
        '</div>'
        '</aside>'

        # Hamburger JS
        '<script>'
        'function openMobileDrawer(){'
        '  document.getElementById("mobile-drawer").style.transform="translateX(0)";'
        '  document.getElementById("mobile-drawer-overlay").style.display="block";'
        '  document.body.style.overflow="hidden";'
        '}'
        'function closeMobileDrawer(){'
        '  document.getElementById("mobile-drawer").style.transform="translateX(-100%)";'
        '  document.getElementById("mobile-drawer-overlay").style.display="none";'
        '  document.body.style.overflow="";'
        '}'
        # Highlight active nav link in drawer based on current path
        '(function(){'
        '  var p=window.location.pathname;'
        '  document.querySelectorAll("#mobile-drawer nav a").forEach(function(a){'
        '    var href=a.getAttribute("href");'
        '    if(href&&(p===href||p.startsWith(href+"/")&&href!="/")){'
        '      a.style.background="rgba(255,255,255,0.08)";a.style.color="#fff";'
        '    }'
        '  });'
        '})();'
        # ESC key closes drawer
        'document.addEventListener("keydown",function(e){'
        '  if(e.key==="Escape")closeMobileDrawer();'
        '});'
        '</script>'
    )

# ── Per-user data encryption ───────────────────────────────────────────────────
# Key is derived from SECRET_KEY + user_id so each user's data uses a distinct key.
# This protects against raw database theft; the server itself can decrypt (v1 trade-off).

def _data_fernet(user_id: str):
    """Return a Fernet instance keyed to this user. Returns None if cryptography not installed."""
    if not _FERNET_AVAILABLE:
        return None
    raw = hashlib.pbkdf2_hmac(
        "sha256",
        (app.secret_key + user_id).encode(),
        b"mighty-account-data-v1",
        100_000,
    )
    return Fernet(base64.urlsafe_b64encode(raw))

def _cred_fernet(user_id: str):
    """Fernet key for credential encryption — different salt from account data."""
    if not _FERNET_AVAILABLE:
        return None
    raw = hashlib.pbkdf2_hmac(
        "sha256",
        (app.secret_key + user_id).encode(),
        b"mighty-credentials-v1",
        100_000,
    )
    return Fernet(base64.urlsafe_b64encode(raw))

# ── AI field discovery (Gemini Flash via google-genai SDK) ───────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
try:
    from google import genai as _genai_sdk
    _claude = _genai_sdk.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
except ImportError:
    _claude = None

# ── Candidate snippet extraction ─────────────────────────────────────────────
# Trigger words indicating nearby text likely contains an account fact.
# We extract windows around each hit before calling Gemini, replacing raw page
# blobs with focused excerpts. Improves precision and cuts token cost.
SNIPPET_TRIGGERS = [
    "expires", "expiration", "expiry", "valid through", "valid until",
    "valid thru", "use by", "book by", "fly by", "book and fly by",
    "certificate", "voucher", "e-credit", "ecredit", "travel fund",
    "award", "benefit", "companion", "upgrade", "free night",
    "points", "miles", "balance", "rewards", "cash back",
    "due", "due date", "payment due", "autopay", "auto pay",
    "available", "remaining", "redeemable",
    "status", "tier", "medallion", "elite",
    "offer", "promotion", "bonus", "anniversary",
    "plan", "renewal", "billing", "subscription",
    "amount due", "total due", "minimum payment",
    "credit limit", "available credit",
]

# High-value triggers: category-specific terms that strongly predict account facts
# rather than navigation copy. Scored 4× vs generic triggers (2×) in the block scorer.
_HIGH_VALUE_TRIGGERS: frozenset = frozenset([
    "companion", "certificate", "valid through", "valid until", "valid thru",
    "ecredit", "e-credit", "travel fund", "travel credit",
    "minimum payment", "amount due", "total due", "payment due",
    "upgrade", "global upgrade", "regional upgrade", "suite night",
    "free night", "lounge", "priority pass",
    "medallion", "elite", "autopay", "auto pay",
    "cash back", "annual fee", "statement credit",
    "book by", "fly by", "expires", "expiry", "expiration",
])

# Matches values likely to appear in account pages: dollar amounts, numbers with
# commas/decimals, compact dates (22JUL2024), ISO dates, and written month-name
# dates like "Jan 15, 2027" or "August 31 2026".
_SNIPPET_VALUE_RE = re.compile(
    r'[$€£]\s*\d[\d,\.]*'           # currency: $187, €50
    r'|\b\d[\d,\.]*\b'              # plain numbers: 45,320 / 157.43
    r'|\b\d{1,2}[A-Za-z]{3}\d{4}\b' # compact date: 22JUL2024
    r'|\b\d{4}-\d{2}-\d{2}\b'       # ISO date: 2026-07-15
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}'  # Aug 14, 2026
    , re.IGNORECASE
)

def _extract_candidate_snippets(
    raw_text: str,
    context_lines: int = 8,
    max_blocks: int = 25,
    hint_phrases: list[str] | None = None,
) -> str:
    """Extract and score line-based blocks around trigger words.

    Algorithm:
      1. Split text into lines; find lines containing any SNIPPET_TRIGGER.
         If hint_phrases are provided (from extraction_hints for this site),
         those lines are force-included and receive a scorer bonus.
      2. Expand each hit line into a ±context_lines block.
      3. Merge blocks that are close together (avoids tiny isolated fragments).
      4. Score each block: trigger-word density + value-pattern count, penalise
         long-line marketing copy. Known hint phrases add +5.0 per match.
      5. Keep the top max_blocks blocks by score; re-sort by original position
         so the returned text reads in document order.
      6. Join with '···' separators and cap at 20k chars.

    Falls back to the first 8 k chars if no triggers match.
    """
    if not raw_text:
        return ""

    lines = raw_text.splitlines()
    lower_lines = [ln.lower() for ln in lines]
    n = len(lines)

    # Step 1 — find hit line indices from general triggers
    hit_set: set[int] = set()
    for trigger in SNIPPET_TRIGGERS:
        for i, ll in enumerate(lower_lines):
            if trigger in ll:
                hit_set.add(i)

    # Force-include lines matching known extraction hints for this site.
    # These are previously successful trigger phrases stored in extraction_hints.
    _hint_lower: list[str] = [p.lower() for p in (hint_phrases or [])]
    if _hint_lower:
        for i, ll in enumerate(lower_lines):
            if any(h in ll for h in _hint_lower):
                hit_set.add(i)

    if not hit_set:
        return raw_text[:8_000]

    # Step 2 — expand hits into (start, end) index ranges
    ranges: list[tuple[int, int]] = [
        (max(0, i - context_lines), min(n, i + context_lines + 1))
        for i in sorted(hit_set)
    ]

    # Step 3 — merge overlapping / adjacent ranges (gap ≤ 3 lines)
    merged: list[tuple[int, int]] = []
    cs, ce = ranges[0]
    for s, e in ranges[1:]:
        if s <= ce + 3:
            ce = max(ce, e)
        else:
            merged.append((cs, ce))
            cs, ce = s, e
    merged.append((cs, ce))

    # Step 4 — score each block
    def _score(s: int, e: int) -> float:
        block_lines = lines[s:e]
        block_lower = "\n".join(block_lines).lower()
        # High-value category-specific triggers (companion, certificate, etc.) → 4×
        high_count = sum(1 for t in _HIGH_VALUE_TRIGGERS if t in block_lower)
        # Generic trigger words → 2×
        generic_count = sum(1 for t in SNIPPET_TRIGGERS if t in block_lower)
        # Value-like patterns (numbers, dates, currency) → 1.5×
        val_count = len(_SNIPPET_VALUE_RE.findall(block_lower))
        # Penalise dense marketing prose (very long average line = wall of text)
        avg_len = sum(len(ln) for ln in block_lines) / max(len(block_lines), 1)
        prose_penalty = max(0.0, (avg_len - 100) / 150)
        # Bonus for blocks matching known extraction hints (previously successful phrases)
        hint_bonus = 5.0 * sum(1 for h in _hint_lower if h in block_lower)
        return high_count * 4.0 + generic_count * 2.0 + val_count * 1.5 - prose_penalty + hint_bonus

    scored = sorted(merged, key=lambda r: _score(*r), reverse=True)
    top = sorted(scored[:max_blocks])  # re-sort by position for coherent output

    return "\n\n···\n\n".join("\n".join(lines[s:e]) for s, e in top)[:20_000]


# ── Category schemas ──────────────────────────────────────────────────────────
# Maps each account source to a category with priority field hints.
# Injected into the Gemini prompt so it knows what to look for, not just what
# to exclude — avoids blind extraction and aligns output to meaningful schemas.
_CATEGORY_SCHEMAS: dict = {
    "travel_loyalty": {
        "sources": {
            "delta", "southwest", "united", "american_air", "alaska_air",
            "marriott", "hilton", "hyatt", "ihg", "wyndham",
            "british_airways", "air_france", "jetblue", "frontier", "spirit",
            "accor", "choice_hotels", "best_western",
        },
        "name": "travel loyalty program",
        "priority_fields": (
            "elite status tier name • points/miles balance • "
            "companion certificate or pass (with expiry) • "
            "upgrade certificates (count + expiry) • "
            "free night certificates (count + expiry) • "
            "travel credits or eCredits (amount + expiry) • "
            "progress toward next status tier • "
            "upcoming trips (future dates only)"
        ),
    },
    "credit_card": {
        "sources": {"amex", "chase", "capital_one", "discover", "citi", "bofa", "wells_fargo", "barclays", "synchrony", "apple_card", "usaa"},
        "name": "credit card",
        "priority_fields": (
            "current balance and available credit • "
            "minimum payment due and due date • autopay status • "
            "rewards/points balance • "
            "annual credits (dining, travel, streaming) — remaining amount and reset/expiry date • "
            "personalized offers with specific deadline and reward amount"
        ),
    },
    "utilities": {
        "sources": {"xfinity", "pa_utilities", "att", "att_wireless", "pge", "sdge", "verizon", "tmobile", "comcast", "spectrum"},
        "name": "utility or telecom account",
        "priority_fields": "amount due and due date • autopay status • current plan • data usage and remaining",
    },
    "subscription": {
        "sources": {"netflix", "hulu", "spotify", "disney_plus"},
        "name": "subscription service",
        "priority_fields": "current plan name • next renewal date and price • payment method",
    },
    "banking": {
        "sources": {"fidelity", "schwab", "paypal", "sfcu", "chase_bank", "bofa_bank", "ally", "sofi", "robinhood"},
        "name": "financial account",
        "priority_fields": "account balance • available balance • portfolio value",
    },
    "health": {
        "sources": {"pamf"},
        "name": "healthcare account",
        "priority_fields": "upcoming appointments • active prescriptions",
    },
    "shopping": {
        "sources": {"amazon", "target", "costco", "starbucks", "ticketmaster", "walmart", "bestbuy", "doordash", "instacart", "uber_eats"},
        "name": "retail or rewards account",
        "priority_fields": "rewards balance • membership status and renewal date",
    },
    "insurance": {
        "sources": {"geico", "progressive", "statefarm", "allstate", "aaa", "anthem", "bluecross", "cigna", "aetna", "kaiser"},
        "name": "insurance account",
        "priority_fields": (
            "policy number and status • premium amount and due date • "
            "coverage details • deductible remaining • next renewal date"
        ),
    },
    "automotive": {
        "sources": {"tesla", "ford", "gm", "bmw", "honda", "toyota", "ez_pass", "fastrak", "sunpass"},
        "name": "automotive or transportation account",
        "priority_fields": (
            "account balance • auto-replenish threshold • "
            "vehicle registration expiry • next service due"
        ),
    },
}


# Maps intent context → field key fragments that are relevant
# moved to mighty.scoring


# Expected fields per category — used for coverage gap detection
_EXPECTED_FIELDS: dict[str, dict[str, str]] = {
    "travel_loyalty": {
        "elite_status":   "Elite/tier status",
        "miles_balance":  "Miles or points balance",
        "certificates":   "Free night or companion certificates",
        "travel_credits": "Travel or ancillary credits",
        "upgrades":       "Upgrade certificates",
        "expiry_date":    "Status or cert expiry",
        "upcoming_trips": "Upcoming reservations",
    },
    "credit_card": {
        "current_balance": "Current balance",
        "payment_due_date":"Payment due date",
        "credit_limit":    "Credit limit",
        "rewards_balance": "Points or cashback",
        "annual_fee":      "Annual fee / renewal",
        "statement_credits":"Statement credits",
    },
    "banking": {
        "checking_balance":"Checking balance",
        "savings_balance": "Savings balance",
        "interest_rate":   "APY / interest rate",
        "monthly_fee":     "Monthly fee",
    },
    "utilities": {
        "amount_due":  "Amount due",
        "due_date":    "Due date",
        "auto_pay":    "Auto-pay status",
        "plan":        "Service plan",
        "usage":       "Usage this period",
    },
    "insurance": {
        "policy_number": "Policy number",
        "premium":       "Premium / payment",
        "next_payment":  "Next payment date",
        "coverage":      "Coverage type",
        "expiry_date":   "Policy expiry",
    },
    "shopping": {
        "membership_status": "Membership status",
        "rewards_balance":   "Rewards balance",
        "renewal_date":      "Renewal date",
        "membership_fee":    "Membership fee",
    },
    "automotive": {
        "account_balance": "Account balance",
        "vehicle":         "Vehicle",
        "subscription":    "Active subscriptions",
        "warranty":        "Warranty expiry",
    },
}


def _get_category_schema(source: str) -> dict | None:
    """Return the _CATEGORY_SCHEMAS entry for a given source key, or None."""
    for schema in _CATEGORY_SCHEMAS.values():
        if source in schema.get("sources", set()):
            return schema
    return None


# ── Site Connector Registry ───────────────────────────────────────────────────
# Deterministic JSON path extractors for known loyalty sites.
# Paths are dot-notation into the parsed API/embedded-state JSON.
# The first path that yields a non-empty value wins.
#
# These are seeded from known API shapes and auto-updated via
# _record_connector_candidates() when Gemini successfully extracts a field.
#
# Priority: connector paths are tried BEFORE Gemini. On a cache hit,
# Gemini is skipped entirely (faster, cheaper, zero hallucination risk).
#
SITE_CONNECTORS: dict[str, list[dict]] = {
    # Each entry is a list of field specs:
    #   key        — internal field key (matches _CATEGORY_SCHEMAS fields)
    #   label      — display label
    #   paths      — ordered list of dot-notation paths to try
    "delta": [
        {
            "key": "elite_status", "label": "Medallion Status",
            "paths": [
                "data.member.medallionStatus",
                "member.medallionStatus",
                "loyalty.tier",
                "account.tier",
            ],
        },
        {
            "key": "points_balance", "label": "SkyMiles Balance",
            "paths": [
                "data.member.skymiles",
                "data.member.miles",
                "member.skymiles",
                "member.miles",
                "loyalty.miles",
                "account.miles",
            ],
        },
        {
            "key": "ecredit_balance", "label": "eCredit Balance",
            "paths": [
                "data.wallet.totalEcreditValue",
                "wallet.totalEcreditValue",
                "ecredits.totalValue",
            ],
        },
    ],
    "united": [
        {
            "key": "elite_status", "label": "MileagePlus Status",
            "paths": [
                "mpAccount.eliteStatus",
                "mileagePlusAccount.eliteLevel",
                "account.eliteStatus",
                "loyalty.tier",
            ],
        },
        {
            "key": "points_balance", "label": "MileagePlus Miles",
            "paths": [
                "mpAccount.balance",
                "mileagePlusAccount.accountBalance",
                "account.miles",
                "loyalty.miles",
            ],
        },
    ],
    "hilton": [
        {
            "key": "elite_status", "label": "Hilton Honors Status",
            "paths": [
                "loyalty.tier",
                "guest.hhonors.tier",
                "data.guest.hhonors.tier",
                "guestInfo.hhonors.tier",
                "props.pageProps.guestInfo.hhonors.tier",
                "loyaltyProfile.tier",
                "hhonors.tier",
            ],
        },
        {
            "key": "points_balance", "label": "Hilton Honors Points",
            "paths": [
                "loyalty.points",
                "loyalty.pointsBalance",
                "guest.hhonors.points",
                "data.guest.hhonors.points",
                "guestInfo.hhonors.points",
                "props.pageProps.guestInfo.hhonors.points",
                "hhonors.points",
                "hhonors.pointsBalance",
            ],
        },
    ],
    "marriott": [
        {
            "key": "elite_status", "label": "Bonvoy Status",
            "paths": [
                "loyalty.tier",
                "bonvoyAccount.tier",
                "member.eliteStatus",
                "profile.tier",
                "account.tier",
                "props.pageProps.memberProfile.tier",
            ],
        },
        {
            "key": "points_balance", "label": "Bonvoy Points",
            "paths": [
                "loyalty.points",
                "bonvoyAccount.points",
                "member.points",
                "props.pageProps.memberProfile.points",
                "account.points",
            ],
        },
    ],
    "hyatt": [
        {
            "key": "elite_status", "label": "World of Hyatt Status",
            "paths": [
                "loyalty.tier",
                "member.tier",
                "account.tier",
                "props.pageProps.member.tier",
            ],
        },
        {
            "key": "points_balance", "label": "World of Hyatt Points",
            "paths": [
                "loyalty.points",
                "member.points",
                "account.points",
                "props.pageProps.member.points",
            ],
        },
    ],
    "southwest": [
        {
            "key": "elite_status", "label": "A-List Status",
            "paths": [
                "account.tierName",
                "loyalty.tier",
                "member.tierStatus",
                "customer.tierLevel",
            ],
        },
        {
            "key": "points_balance", "label": "Rapid Rewards Points",
            "paths": [
                "account.pointsBalance",
                "loyalty.points",
                "member.points",
                "customer.points",
            ],
        },
    ],
    "amex": [
        {
            "key": "points_balance", "label": "Membership Rewards Points",
            "paths": [
                "membershipRewards.points",
                "loyalty.balance",
                "accountSummary.rewardsBalance",
                "rewards.pointsBalance",
            ],
        },
        {
            "key": "statement_balance", "label": "Statement Balance",
            "paths": [
                "accountSummary.statementBalance",
                "account.statementBalance",
                "balance.statementBalance",
            ],
        },
    ],
    "chase": [
        {
            "key": "points_balance", "label": "Ultimate Rewards Points",
            "paths": [
                "ultimateRewards.balance",
                "rewards.pointsBalance",
                "loyalty.balance",
            ],
        },
        {
            "key": "statement_balance", "label": "Statement Balance",
            "paths": [
                "accountSummary.statementBalance",
                "account.statementBalance",
            ],
        },
    ],
}


def _resolve_json_path(obj, path: str):
    """Walk a dot-notation path through a nested JSON object.
    Returns the value at that path, or None if any segment is missing.
    Handles list indexing with integer segments (e.g. 'items.0.value').
    """
    cur = obj
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError, TypeError):
                return None
        else:
            return None
    return cur


def try_connector_paths(source: str, json_data: str) -> list[dict]:
    """Try deterministic JSON path extractors for known loyalty sites.

    Returns a list of field dicts in the same format as claude_discover_fields,
    or an empty list if the source has no connector or no paths match.

    Connector fields carry confidence=0.99 and from_connector=True so they
    sort ahead of Gemini fields in _save_discovered_fields dedup.
    """
    specs = SITE_CONNECTORS.get(source)
    if not specs:
        return []
    try:
        obj = json.loads(json_data)
    except (ValueError, TypeError):
        return []

    _EMPTY = frozenset({"", "0", "-", "–", "—", "null", "none", "unknown"})
    fields: list[dict] = []
    for spec in specs:
        field_key = spec["key"]
        label     = spec["label"]
        for path in spec["paths"]:
            val = _resolve_json_path(obj, path)
            if val is None:
                continue
            val_str = str(val).strip()
            if val_str.lower() in _EMPTY or not val_str:
                continue
            # Found a real value
            fields.append({
                "key":            field_key,
                "label":          label,
                "value":          val_str,
                "confidence":     0.99,
                "source_snippet": f"[connector:{path}]",
                "from_api":       True,
                "from_connector": True,
                "connector_path": path,
            })
            break  # first matching path wins; don't add duplicates for same field

    return fields


def _find_json_path_for_value(obj, target: str, max_depth: int = 8, _path: str = "") -> str | None:
    """Recursively search a parsed JSON object for the first leaf whose string
    representation matches *target* (case-insensitive strip match).

    Returns the dot-notation path of the first match, or None.
    Used by _record_connector_candidates to learn new connector paths from
    successful Gemini extractions.
    """
    if max_depth <= 0:
        return None
    t_lower = target.strip().lower()
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{_path}.{k}" if _path else k
            if isinstance(v, (str, int, float)):
                if str(v).strip().lower() == t_lower:
                    return p
            result = _find_json_path_for_value(v, target, max_depth - 1, p)
            if result:
                return result
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            p = f"{_path}.{i}" if _path else str(i)
            result = _find_json_path_for_value(v, target, max_depth - 1, p)
            if result:
                return result
    return None


def _record_connector_candidates(source: str, raw_json: str, fields: list[dict]) -> None:
    """After a successful Gemini extraction, search the source JSON for each
    extracted field value and log the dot-notation path.

    These log lines are candidates for promotion into SITE_CONNECTORS:
      [ConnectorCandidate] delta.elite_status → data.member.medallionStatus = 'Gold'
    """
    if not fields or not raw_json:
        return
    try:
        obj = json.loads(raw_json)
    except Exception:
        return

    for field in fields:
        if field.get("from_connector"):
            continue  # already came from a connector path
        val = str(field.get("value", "")).strip()
        key = field.get("key", "")
        if not val or not key or val.lower() in ("", "-", "–", "—"):
            continue
        path = _find_json_path_for_value(obj, val)
        if path:
            print(
                f"[ConnectorCandidate] {source}.{key} → {path} = {val[:50]!r}",
                flush=True,
            )


SOURCE_CAPABILITIES: dict[str, dict] = {
    # ── Airlines ─────────────────────────────────────────────────────────────
    "delta": {
        "display_name": "Delta SkyMiles",
        "domain": "delta.com",
        "category": "airline",
        "benefit_types": ["miles", "ecredit", "companion_cert", "upgrade_cert", "medallion_status"],
        "key_pages": [
            "/us/en/my-account/overview",
            "/us/en/my-account/skymiles",
            "/myprofile/credits",
            "/myprofile/documents",
        ],
        "login_url": "https://www.delta.com/us/en/sign-in/overview",
    },
    "united": {
        "display_name": "United MileagePlus",
        "domain": "united.com",
        "category": "airline",
        "benefit_types": ["miles", "ecredit", "upgrade_cert", "premier_status"],
        "key_pages": [
            "/en/us/myunited",
            "/en/us/myunited/wallet",
            "/en/us/myunited/mileageplus",
        ],
        "login_url": "https://www.united.com/en/us/myaccount/login",
    },
    "american_air": {
        "display_name": "American Airlines AAdvantage",
        "domain": "aa.com",
        "category": "airline",
        "benefit_types": ["miles", "ecredit", "upgrade_cert", "systemwide_upgrade", "elite_status"],
        "key_pages": [
            "/account/myaccount/",
            "/account/myaccount/travelerinfo/redeemmiles",
            "/account/myaccount/activity/miles/",
        ],
        "login_url": "https://www.aa.com/homePage.do",
    },
    "southwest": {
        "display_name": "Southwest Rapid Rewards",
        "domain": "southwest.com",
        "category": "airline",
        "benefit_types": ["points", "companion_pass", "tier_status"],
        "key_pages": [
            "/account/",
            "/account/upcoming-trips/",
            "/account/rapid-rewards/",
        ],
        "login_url": "https://www.southwest.com/",
    },
    "alaska_air": {
        "display_name": "Alaska Airlines Mileage Plan",
        "domain": "alaskaair.com",
        "category": "airline",
        "benefit_types": ["miles", "elite_status", "companion_fare"],
        "key_pages": [
            "/account/activity",
            "/account/mileage-plan",
            "/account/profile",
        ],
        "login_url": "https://www.alaskaair.com/account/login",
    },
    "jetblue": {
        "display_name": "JetBlue TrueBlue",
        "domain": "jetblue.com",
        "category": "airline",
        "benefit_types": ["points", "tier_status", "travel_bank"],
        "key_pages": [
            "/account/",
            "/trueblue/",
        ],
        "login_url": "https://www.jetblue.com/account/login",
    },
    "frontier": {
        "display_name": "Frontier Airlines FRONTIER Miles",
        "domain": "flyfrontier.com",
        "category": "airline",
        "benefit_types": ["miles", "tier_status", "upgrade_cert"],
        "key_pages": ["/account/"],
        "login_url": "https://www.flyfrontier.com/account/",
    },
    "spirit": {
        "display_name": "Spirit Airlines Free Spirit",
        "domain": "spirit.com",
        "category": "airline",
        "benefit_types": ["points", "tier_status"],
        "key_pages": ["/myaccount/"],
        "login_url": "https://www.spirit.com/myaccount/",
    },
    "air_canada": {
        "display_name": "Air Canada Aeroplan",
        "domain": "aircanada.com",
        "category": "airline",
        "benefit_types": ["points", "tier_status", "upgrade_cert"],
        "key_pages": [
            "/en/web/aeroplan/my-account",
            "/en/web/aeroplan/my-account/overview",
        ],
        "login_url": "https://www.aircanada.com/en/web/login",
    },

    # ── Hotels ───────────────────────────────────────────────────────────────
    "marriott": {
        "display_name": "Marriott Bonvoy",
        "domain": "marriott.com",
        "category": "hotel",
        "benefit_types": ["points", "free_night_cert", "suite_night_award", "tier_status"],
        "key_pages": [
            "/loyalty/myAccount/default.mi",
            "/loyalty/myAccount/rewards/redeemPoints.mi",
            "/loyalty/myAccount/dashboard/index.mi",
        ],
        "login_url": "https://www.marriott.com/loyalty/myAccount/default.mi",
    },
    "hilton": {
        "display_name": "Hilton Honors",
        "domain": "hilton.com",
        "category": "hotel",
        "benefit_types": ["points", "free_night_reward", "tier_status"],
        "key_pages": [
            "/en/hilton-honors/guest/my-account/",
            "/en/hiltonhonors/account/",
        ],
        "login_url": "https://www.hilton.com/en/hilton-honors/login/",
    },
    "hyatt": {
        "display_name": "World of Hyatt",
        "domain": "hyatt.com",
        "category": "hotel",
        "benefit_types": ["points", "free_night_award", "suite_upgrade_award", "tier_status"],
        "key_pages": [
            "/woh/account/activity",
            "/woh/account/myAwards",
        ],
        "login_url": "https://www.hyatt.com/en-US/account/login",
    },
    "ihg": {
        "display_name": "IHG One Rewards",
        "domain": "ihg.com",
        "category": "hotel",
        "benefit_types": ["points", "milestone_reward_night", "tier_status"],
        "key_pages": [
            "/us/en/ihg-one-rewards/account",
            "/us/en/ihg-one-rewards/account/activity",
        ],
        "login_url": "https://www.ihg.com/us/en/ihg-one-rewards/login",
    },
    "wyndham": {
        "display_name": "Wyndham Rewards",
        "domain": "wyndhamhotels.com",
        "category": "hotel",
        "benefit_types": ["points", "free_night_cert", "tier_status"],
        "key_pages": [
            "/account/",
            "/account/dashboard",
        ],
        "login_url": "https://www.wyndhamhotels.com/account/login",
    },
    "choice_hotels": {
        "display_name": "Choice Privileges",
        "domain": "choicehotels.com",
        "category": "hotel",
        "benefit_types": ["points", "free_night_cert", "tier_status"],
        "key_pages": ["/account/"],
        "login_url": "https://www.choicehotels.com/account/login",
    },
    "best_western": {
        "display_name": "Best Western Rewards",
        "domain": "bestwestern.com",
        "category": "hotel",
        "benefit_types": ["points", "free_night_cert", "tier_status"],
        "key_pages": ["/account/"],
        "login_url": "https://www.bestwestern.com/en_US/account/login.html",
    },
    "radisson": {
        "display_name": "Radisson Rewards",
        "domain": "radissonhotels.com",
        "category": "hotel",
        "benefit_types": ["points", "tier_status"],
        "key_pages": ["/en-us/account/"],
        "login_url": "https://www.radissonhotels.com/en-us/account/login",
    },
    "mgm_rewards": {
        "display_name": "MGM Rewards",
        "domain": "mgmresorts.com",
        "category": "hotel",
        "benefit_types": ["points", "tier_status", "resort_credits"],
        "key_pages": [
            "/en-us/loyalty.html",
            "/en-us/mlife/account.html",
        ],
        "login_url": "https://www.mgmresorts.com/en-us/account/login.html",
    },

    # ── Credit Cards & Banks ──────────────────────────────────────────────────
    "amex": {
        "display_name": "American Express",
        "domain": "americanexpress.com",
        "category": "credit_card",
        "benefit_types": ["membership_rewards", "travel_credit", "dining_credit", "hotel_credit",
                         "airline_fee_credit", "purchase_protection", "extended_warranty",
                         "global_entry_credit", "digital_entertainment_credit", "streaming_credit"],
        "key_pages": [
            "/dashboard",
            "/account/login",
        ],
        "login_url": "https://www.americanexpress.com/en-us/account/login",
    },
    "chase": {
        "display_name": "Chase",
        "domain": "chase.com",
        "category": "credit_card",
        "benefit_types": ["ultimate_rewards", "travel_credit", "dining_credit", "hotel_credit",
                         "purchase_protection", "trip_delay", "cell_phone_protection"],
        "key_pages": [
            "/account-summary",
            "/account/cards",
        ],
        "login_url": "https://secure.chase.com/web/auth/#/pages/login/simple-challenge",
    },
    "citi": {
        "display_name": "Citi",
        "domain": "citi.com",
        "category": "credit_card",
        "benefit_types": ["thank_you_points", "travel_credit", "purchase_protection",
                         "extended_warranty", "price_protection", "cash_back"],
        "key_pages": [
            "/US/JRS/portal/53004.do",
            "/US/JRS/portal/53004.do?Screen=ACoverMicro&JFP_TOKEN=FLOW",
        ],
        "login_url": "https://www.citi.com/",
    },
    "capital_one": {
        "display_name": "Capital One",
        "domain": "capitalone.com",
        "category": "credit_card",
        "benefit_types": ["venture_miles", "cash_back", "purchase_protection", "travel_credit"],
        "key_pages": [
            "/account-summary",
            "/card-account/",
        ],
        "login_url": "https://www.capitalone.com/",
    },
    "discover": {
        "display_name": "Discover",
        "domain": "discover.com",
        "category": "credit_card",
        "benefit_types": ["cash_back", "cashback_bonus", "purchase_protection", "extended_warranty"],
        "key_pages": [
            "/cardmembersvcs/acctsvcs/",
            "/cardmembersvcs/acctsvcs/home",
        ],
        "login_url": "https://www.discover.com/",
    },
    "wells_fargo": {
        "display_name": "Wells Fargo",
        "domain": "wellsfargo.com",
        "category": "bank",
        "benefit_types": ["rewards_points", "cash_back", "checking_balance", "savings_balance"],
        "key_pages": [
            "/accounts/start.go",
            "/accounts/summary.jhtml",
        ],
        "login_url": "https://www.wellsfargo.com/",
    },
    "bofa": {
        "display_name": "Bank of America",
        "domain": "bankofamerica.com",
        "category": "bank",
        "benefit_types": ["preferred_rewards", "cash_back", "travel_rewards", "checking_balance"],
        "key_pages": [
            "/myfinanciallife/overview.go",
            "/accounts/static/en-us/html/accts-overview.html",
        ],
        "login_url": "https://www.bankofamerica.com/",
    },
    "us_bank": {
        "display_name": "U.S. Bank",
        "domain": "usbank.com",
        "category": "bank",
        "benefit_types": ["rewards_points", "cash_back", "altitude_points"],
        "key_pages": [
            "/dashboard",
            "/account-home",
        ],
        "login_url": "https://onlinebanking.usbank.com/Auth/Login",
    },
    "barclays": {
        "display_name": "Barclays",
        "domain": "barclaysus.com",
        "category": "credit_card",
        "benefit_types": ["miles", "cash_back", "travel_credit"],
        "key_pages": ["/login/"],
        "login_url": "https://www.barclaysus.com/login/",
    },
    "paypal": {
        "display_name": "PayPal",
        "domain": "paypal.com",
        "category": "payment",
        "benefit_types": ["cash_balance", "rewards", "paypal_credit"],
        "key_pages": [
            "/myaccount/home",
            "/myaccount/money",
        ],
        "login_url": "https://www.paypal.com/signin",
    },
    "venmo": {
        "display_name": "Venmo",
        "domain": "venmo.com",
        "category": "payment",
        "benefit_types": ["cash_balance", "rewards"],
        "key_pages": ["/account/"],
        "login_url": "https://venmo.com/account/sign-in/",
    },
    "fidelity": {
        "display_name": "Fidelity",
        "domain": "fidelity.com",
        "category": "investment",
        "benefit_types": ["portfolio_value", "rewards_visa"],
        "key_pages": [
            "/ftgw/atm/NBPreLogin.do",
            "/summary",
        ],
        "login_url": "https://login.fidelity.com/ftgw/Fas/Fidelity/RtlCust/Login/Init",
    },
    "schwab": {
        "display_name": "Charles Schwab",
        "domain": "schwab.com",
        "category": "investment",
        "benefit_types": ["portfolio_value", "cash_balance"],
        "key_pages": [
            "/client-home",
            "/secure/cc#/client-home",
        ],
        "login_url": "https://www.schwab.com/",
    },
    "vanguard": {
        "display_name": "Vanguard",
        "domain": "vanguard.com",
        "category": "investment",
        "benefit_types": ["portfolio_value"],
        "key_pages": ["/us/MyVanguard"],
        "login_url": "https://investor.vanguard.com/home",
    },
    "sofi": {
        "display_name": "SoFi",
        "domain": "sofi.com",
        "category": "bank",
        "benefit_types": ["cash_balance", "rewards", "savings_apy"],
        "key_pages": [
            "/account/",
            "/banking/",
        ],
        "login_url": "https://www.sofi.com/account/",
    },
    "sfcu": {
        "display_name": "Stanford FCU",
        "domain": "sfcu.org",
        "category": "bank",
        "benefit_types": ["account_balance", "rewards"],
        "key_pages": ["/"],
        "login_url": "https://www.sfcu.org/",
    },

    # ── Telecom & Cable ───────────────────────────────────────────────────────
    "xfinity": {
        "display_name": "Xfinity",
        "domain": "xfinity.com",
        "category": "telecom",
        "benefit_types": ["data_usage", "plan_details", "active_services", "billing_amount"],
        "key_pages": [
            "/account/dashboard",
            "/account/",
            "/account/billing/",
        ],
        "login_url": "https://login.xfinity.com/login",
    },
    "att": {
        "display_name": "AT&T",
        "domain": "att.com",
        "category": "telecom",
        "benefit_types": ["plan_details", "data_usage", "billing_amount", "rewards"],
        "key_pages": [
            "/my/account/",
            "/my/account/overview",
        ],
        "login_url": "https://www.att.com/my/",
    },
    "att_wireless": {
        "display_name": "AT&T Wireless",
        "domain": "att.com",
        "category": "telecom",
        "benefit_types": ["plan_details", "data_usage", "billing_amount", "autopay_discount", "rewards"],
        "key_pages": [
            "/my/account/",
            "/my/account/devices",
            "/my/account/payment",
        ],
        "login_url": "https://www.att.com/my/",
    },
    "verizon": {
        "display_name": "Verizon",
        "domain": "verizon.com",
        "category": "telecom",
        "benefit_types": ["plan_details", "data_usage", "billing_amount", "rewards"],
        "key_pages": [
            "/account/home/",
            "/myhome/landing.html",
        ],
        "login_url": "https://www.verizon.com/home/myaccount/",
    },
    "tmobile": {
        "display_name": "T-Mobile",
        "domain": "t-mobile.com",
        "category": "telecom",
        "benefit_types": ["plan_details", "data_usage", "billing_amount", "tuesday_rewards"],
        "key_pages": [
            "/account/landing",
            "/account/line-summary",
        ],
        "login_url": "https://account.t-mobile.com/",
    },
    "spectrum": {
        "display_name": "Spectrum",
        "domain": "spectrum.com",
        "category": "telecom",
        "benefit_types": ["plan_details", "billing_amount", "active_services"],
        "key_pages": ["/account/"],
        "login_url": "https://www.spectrum.com/account",
    },
    "cox": {
        "display_name": "Cox",
        "domain": "cox.com",
        "category": "telecom",
        "benefit_types": ["plan_details", "data_usage", "billing_amount"],
        "key_pages": ["/residential/account.html"],
        "login_url": "https://myaccount.cox.com/mycox/",
    },

    # ── Streaming & Entertainment ─────────────────────────────────────────────
    "netflix": {
        "display_name": "Netflix",
        "domain": "netflix.com",
        "category": "streaming",
        "benefit_types": ["subscription", "plan_details", "membership_expiry"],
        "key_pages": ["/YourAccount"],
        "login_url": "https://www.netflix.com/login",
    },
    "disney_plus": {
        "display_name": "Disney+",
        "domain": "disneyplus.com",
        "category": "streaming",
        "benefit_types": ["subscription", "plan_details"],
        "key_pages": [
            "/account",
            "/account/subscription",
        ],
        "login_url": "https://www.disneyplus.com/login",
    },
    "hulu": {
        "display_name": "Hulu",
        "domain": "hulu.com",
        "category": "streaming",
        "benefit_types": ["subscription", "plan_details"],
        "key_pages": [
            "/account",
            "/account/billing",
        ],
        "login_url": "https://auth.hulu.com/web/login",
    },
    "spotify": {
        "display_name": "Spotify",
        "domain": "spotify.com",
        "category": "streaming",
        "benefit_types": ["subscription", "plan_details", "premium_features"],
        "key_pages": [
            "/account/overview",
            "/account/subscription",
        ],
        "login_url": "https://accounts.spotify.com/en/login",
    },
    "max": {
        "display_name": "Max (HBO)",
        "domain": "max.com",
        "category": "streaming",
        "benefit_types": ["subscription", "plan_details"],
        "key_pages": [
            "/settings",
            "/settings/subscription",
        ],
        "login_url": "https://www.max.com/",
    },
    "peacock": {
        "display_name": "Peacock",
        "domain": "peacocktv.com",
        "category": "streaming",
        "benefit_types": ["subscription", "plan_details"],
        "key_pages": [
            "/account",
            "/account/plan",
        ],
        "login_url": "https://www.peacocktv.com/account",
    },
    "paramount_plus": {
        "display_name": "Paramount+",
        "domain": "paramountplus.com",
        "category": "streaming",
        "benefit_types": ["subscription", "plan_details"],
        "key_pages": ["/account"],
        "login_url": "https://www.paramountplus.com/account",
    },
    "amazon_prime": {
        "display_name": "Amazon Prime",
        "domain": "amazon.com",
        "category": "streaming",
        "benefit_types": ["prime_benefits", "prime_video", "free_shipping", "membership_expiry"],
        "key_pages": [
            "/gp/primecentral/",
            "/gp/css/account/view.html",
        ],
        "login_url": "https://www.amazon.com/ap/signin",
    },
    "apple_tv": {
        "display_name": "Apple TV+",
        "domain": "tv.apple.com",
        "category": "streaming",
        "benefit_types": ["subscription", "plan_details"],
        "key_pages": ["/settings"],
        "login_url": "https://tv.apple.com/",
    },
    "youtube_premium": {
        "display_name": "YouTube Premium",
        "domain": "youtube.com",
        "category": "streaming",
        "benefit_types": ["subscription", "plan_details", "membership_expiry"],
        "key_pages": ["/account_memberships"],
        "login_url": "https://accounts.google.com/",
    },

    # ── Retail & Shopping ─────────────────────────────────────────────────────
    "amazon": {
        "display_name": "Amazon",
        "domain": "amazon.com",
        "category": "retail",
        "benefit_types": ["prime_benefits", "gift_card_balance", "reward_balance"],
        "key_pages": [
            "/cpe/yourpayments/wallet",
            "/gp/css/account/view.html",
            "/cpe/giftcard/balance",
        ],
        "login_url": "https://www.amazon.com/ap/signin",
    },
    "target": {
        "display_name": "Target Circle",
        "domain": "target.com",
        "category": "retail",
        "benefit_types": ["circle_earnings", "reward_balance", "membership_expiry"],
        "key_pages": [
            "/account/profile",
            "/account/wallet",
        ],
        "login_url": "https://www.target.com/account",
    },
    "walmart": {
        "display_name": "Walmart+",
        "domain": "walmart.com",
        "category": "retail",
        "benefit_types": ["plus_membership", "rewards", "free_shipping"],
        "key_pages": [
            "/account/profile",
            "/account/walmartplus",
        ],
        "login_url": "https://www.walmart.com/account/login",
    },
    "costco": {
        "display_name": "Costco",
        "domain": "costco.com",
        "category": "retail",
        "benefit_types": ["reward_certificate", "membership_expiry", "executive_reward"],
        "key_pages": [
            "/Warehouse/AccountManagement.aspx",
            "/Warehouse/AccountManagement.aspx?SelectedPage=6",
        ],
        "login_url": "https://www.costco.com/LogonForm",
    },
    "starbucks": {
        "display_name": "Starbucks Rewards",
        "domain": "starbucks.com",
        "category": "retail",
        "benefit_types": ["stars", "free_reward", "tier_status", "gift_card_balance"],
        "key_pages": [
            "/account/profile",
            "/account/stars",
        ],
        "login_url": "https://www.starbucks.com/account/signin",
    },
    "cvs": {
        "display_name": "CVS ExtraCare",
        "domain": "cvs.com",
        "category": "retail",
        "benefit_types": ["extrabucks", "reward_balance", "care_pass"],
        "key_pages": [
            "/account/",
            "/account/extracare/",
        ],
        "login_url": "https://www.cvs.com/account/login/",
    },
    "walgreens": {
        "display_name": "Walgreens myWalgreens",
        "domain": "walgreens.com",
        "category": "retail",
        "benefit_types": ["cash_rewards", "reward_balance", "plus_membership"],
        "key_pages": [
            "/account/v2/summary.jsp",
            "/account/",
        ],
        "login_url": "https://www.walgreens.com/login.jsp",
    },
    "kroger": {
        "display_name": "Kroger",
        "domain": "kroger.com",
        "category": "retail",
        "benefit_types": ["fuel_points", "rewards", "plus_card_savings"],
        "key_pages": [
            "/account/",
            "/savings/for-u/",
        ],
        "login_url": "https://www.kroger.com/account/login",
    },
    "sephora": {
        "display_name": "Sephora Beauty Insider",
        "domain": "sephora.com",
        "category": "retail",
        "benefit_types": ["points", "tier_status", "birthday_reward", "reward_balance"],
        "key_pages": [
            "/profile/",
            "/beauty/beauty-insider",
        ],
        "login_url": "https://www.sephora.com/profile/login/",
    },
    "ulta": {
        "display_name": "Ulta Beauty Rewards",
        "domain": "ulta.com",
        "category": "retail",
        "benefit_types": ["points", "tier_status", "reward_balance", "birthday_reward"],
        "key_pages": [
            "/profile",
            "/ulta/rewards/",
        ],
        "login_url": "https://www.ulta.com/profile",
    },
    "nordstrom": {
        "display_name": "Nordstrom Nordy Club",
        "domain": "nordstrom.com",
        "category": "retail",
        "benefit_types": ["nordstrom_notes", "tier_status", "reward_balance", "early_access"],
        "key_pages": [
            "/account/",
            "/account/points",
        ],
        "login_url": "https://www.nordstrom.com/account/login",
    },
    "macys": {
        "display_name": "Macy's Star Rewards",
        "domain": "macys.com",
        "category": "retail",
        "benefit_types": ["star_rewards", "tier_status", "reward_balance", "cash_bonus"],
        "key_pages": [
            "/account/profile",
            "/account/wallet",
        ],
        "login_url": "https://www.macys.com/account/login",
    },
    "kohls": {
        "display_name": "Kohl's Rewards",
        "domain": "kohls.com",
        "category": "retail",
        "benefit_types": ["kohls_cash", "rewards", "yes2you_points"],
        "key_pages": [
            "/wallet/",
            "/account",
        ],
        "login_url": "https://www.kohls.com/account/login.jsp",
    },
    "bestbuy": {
        "display_name": "Best Buy My Best Buy",
        "domain": "bestbuy.com",
        "category": "retail",
        "benefit_types": ["reward_certificates", "points", "tier_status", "total_tech"],
        "key_pages": [
            "/site/my-best-buy/my-account.page",
            "/profile/",
        ],
        "login_url": "https://www.bestbuy.com/identity/global/signin",
    },
    "homedepot": {
        "display_name": "Home Depot",
        "domain": "homedepot.com",
        "category": "retail",
        "benefit_types": ["pro_xtra_rewards", "reward_balance", "paint_rewards"],
        "key_pages": [
            "/myaccount/",
            "/myaccount/profile",
        ],
        "login_url": "https://www.homedepot.com/auth/view/signin",
    },
    "nike": {
        "display_name": "Nike Membership",
        "domain": "nike.com",
        "category": "retail",
        "benefit_types": ["member_rewards", "early_access", "free_shipping"],
        "key_pages": [
            "/member/",
            "/member/profile",
        ],
        "login_url": "https://www.nike.com/member/login",
    },

    # ── Car Rental ───────────────────────────────────────────────────────────
    "hertz": {
        "display_name": "Hertz Gold Plus Rewards",
        "domain": "hertz.com",
        "category": "car_rental",
        "benefit_types": ["points", "free_day_cert", "tier_status"],
        "key_pages": [
            "/en/rentacar/myaccount/dashboard.jsp",
            "/en/rentacar/myaccount/",
        ],
        "login_url": "https://www.hertz.com/rentacar/myaccount/dashboard.jsp",
    },
    "avis": {
        "display_name": "Avis Preferred",
        "domain": "avis.com",
        "category": "car_rental",
        "benefit_types": ["points", "free_day_cert", "tier_status"],
        "key_pages": [
            "/en/customer-service/manage-my-account",
            "/en/account/",
        ],
        "login_url": "https://www.avis.com/en/account/login",
    },
    "enterprise": {
        "display_name": "Enterprise Plus",
        "domain": "enterprise.com",
        "category": "car_rental",
        "benefit_types": ["points", "free_day_cert", "tier_status"],
        "key_pages": [
            "/car-rental/deeplinkmap.do?link=myProfilePage",
            "/account/profile",
        ],
        "login_url": "https://www.enterprise.com/en/home.html",
    },
    "national": {
        "display_name": "National Emerald Club",
        "domain": "nationalcar.com",
        "category": "car_rental",
        "benefit_types": ["free_day_cert", "tier_status", "emerald_club_days"],
        "key_pages": [
            "/car-rental/deeplinkmap.do?link=myProfilePage",
            "/account/profile",
        ],
        "login_url": "https://www.nationalcar.com/",
    },
    "budget": {
        "display_name": "Budget Fastbreak",
        "domain": "budget.com",
        "category": "car_rental",
        "benefit_types": ["points", "free_day_cert"],
        "key_pages": ["/en/home/tools/account.html"],
        "login_url": "https://www.budget.com/en/home/tools/account.html",
    },

    # ── Food, Delivery & Rideshare ────────────────────────────────────────────
    "uber": {
        "display_name": "Uber / Uber Eats",
        "domain": "uber.com",
        "category": "rideshare",
        "benefit_types": ["cash_balance", "uber_cash", "uber_one", "uber_rewards"],
        "key_pages": [
            "/m/menu",
            "/account/",
        ],
        "login_url": "https://auth.uber.com/v2/",
    },
    "lyft": {
        "display_name": "Lyft",
        "domain": "lyft.com",
        "category": "rideshare",
        "benefit_types": ["rewards", "lyft_pink_membership", "credits"],
        "key_pages": ["/account"],
        "login_url": "https://www.lyft.com/account",
    },
    "doordash": {
        "display_name": "DoorDash",
        "domain": "doordash.com",
        "category": "food_delivery",
        "benefit_types": ["dashpass", "credits", "subscription"],
        "key_pages": [
            "/account/",
            "/account/orders",
        ],
        "login_url": "https://www.doordash.com/account/",
    },
    "grubhub": {
        "display_name": "Grubhub+",
        "domain": "grubhub.com",
        "category": "food_delivery",
        "benefit_types": ["grubhub_plus", "credits", "reward_balance"],
        "key_pages": ["/account/"],
        "login_url": "https://www.grubhub.com/login",
    },
        "chipotle": {
        "display_name": "Chipotle Rewards",
        "domain": "chipotle.com",
        "category": "food_delivery",
        "benefit_types": ["points", "free_entree", "extra_guac"],
        "key_pages": ["/account/"],
        "login_url": "https://www.chipotle.com/login",
    },
    "panera": {
        "display_name": "Panera Sip Club / MyPanera",
        "domain": "panerabread.com",
        "category": "food_delivery",
        "benefit_types": ["points", "free_reward", "sip_club"],
        "key_pages": ["/account/"],
        "login_url": "https://www.panerabread.com/en-us/mypanera/login.html",
    },

    # ── Health & Fitness ──────────────────────────────────────────────────────
    "kaiser": {
        "display_name": "Kaiser Permanente",
        "domain": "kp.org",
        "category": "health",
        "benefit_types": ["plan_details", "deductible_balance", "out_of_pocket_balance", "benefits"],
        "key_pages": [
            "/healthchannel/index.jsp",
            "/bin/public/kpmembers.html",
        ],
        "login_url": "https://kp.org/",
    },
    "aetna": {
        "display_name": "Aetna",
        "domain": "aetna.com",
        "category": "health",
        "benefit_types": ["plan_details", "deductible_balance", "out_of_pocket_balance", "hsa_balance"],
        "key_pages": [
            "/member/",
            "/member/plan-benefits",
        ],
        "login_url": "https://member.aetna.com/",
    },
    "cigna": {
        "display_name": "Cigna",
        "domain": "cigna.com",
        "category": "health",
        "benefit_types": ["plan_details", "deductible_balance", "out_of_pocket_balance", "hsa_balance"],
        "key_pages": [
            "/member/",
            "/individuals-families/member-guide/",
        ],
        "login_url": "https://my.cigna.com/",
    },
    "united_health": {
        "display_name": "UnitedHealthcare",
        "domain": "myuhc.com",
        "category": "health",
        "benefit_types": ["plan_details", "deductible_balance", "out_of_pocket_balance", "hsa_balance"],
        "key_pages": [
            "/member/",
            "/member/dashboard",
        ],
        "login_url": "https://member.uhc.com/",
    },
    "peloton": {
        "display_name": "Peloton",
        "domain": "onepeloton.com",
        "category": "fitness",
        "benefit_types": ["subscription", "plan_details", "membership_expiry"],
        "key_pages": [
            "/profile/",
            "/account",
        ],
        "login_url": "https://www.onepeloton.com/profile",
    },

    # ── Insurance ─────────────────────────────────────────────────────────────
    "state_farm": {
        "display_name": "State Farm",
        "domain": "statefarm.com",
        "category": "insurance",
        "benefit_types": ["policy_details", "deductible", "premium_amount", "renewal_date"],
        "key_pages": [
            "/account/",
            "/insurance/",
        ],
        "login_url": "https://www.statefarm.com/account",
    },
    "geico": {
        "display_name": "GEICO",
        "domain": "geico.com",
        "category": "insurance",
        "benefit_types": ["policy_details", "deductible", "premium_amount", "renewal_date"],
        "key_pages": [
            "/policy-summary/",
            "/account/",
        ],
        "login_url": "https://www.geico.com/login/",
    },
    "progressive": {
        "display_name": "Progressive",
        "domain": "progressive.com",
        "category": "insurance",
        "benefit_types": ["policy_details", "deductible", "premium_amount", "renewal_date", "snapshot_discount"],
        "key_pages": [
            "/overview/",
            "/account/",
        ],
        "login_url": "https://www.progressive.com/login/",
    },
    "allstate": {
        "display_name": "Allstate",
        "domain": "allstate.com",
        "category": "insurance",
        "benefit_types": ["policy_details", "deductible", "premium_amount", "renewal_date", "drivewise_rewards"],
        "key_pages": [
            "/overview.aspx",
            "/account/",
        ],
        "login_url": "https://myaccount.allstate.com/",
    },
    "usaa": {
        "display_name": "USAA",
        "domain": "usaa.com",
        "category": "insurance",
        "benefit_types": ["policy_details", "account_balance", "rewards", "renewal_date"],
        "key_pages": [
            "/my/overview",
            "/my/banking",
        ],
        "login_url": "https://www.usaa.com/",
    },
    "aaa": {
        "display_name": "AAA",
        "domain": "aaa.com",
        "category": "insurance",
        "benefit_types": ["membership_expiry", "roadside_calls_remaining", "tier_status"],
        "key_pages": [
            "/account/",
            "/membership/",
        ],
        "login_url": "https://www.aaa.com/account/",
    },

    # ── Travel & Experiences ──────────────────────────────────────────────────
    "airbnb": {
        "display_name": "Airbnb",
        "domain": "airbnb.com",
        "category": "travel",
        "benefit_types": ["travel_credits", "superhost_status", "gift_card_balance"],
        "key_pages": [
            "/account-settings/personal-info",
            "/account-settings/payments",
        ],
        "login_url": "https://www.airbnb.com/login",
    },
    "expedia": {
        "display_name": "Expedia One Key",
        "domain": "expedia.com",
        "category": "travel",
        "benefit_types": ["one_key_cash", "tier_status", "reward_balance"],
        "key_pages": [
            "/trips",
            "/rewards/",
        ],
        "login_url": "https://www.expedia.com/user/login",
    },
    "booking": {
        "display_name": "Booking.com Genius",
        "domain": "booking.com",
        "category": "travel",
        "benefit_types": ["genius_level", "travel_credits", "tier_status"],
        "key_pages": ["/account/"],
        "login_url": "https://account.booking.com/sign-in",
    },
    "ticketmaster": {
        "display_name": "Ticketmaster",
        "domain": "ticketmaster.com",
        "category": "entertainment",
        "benefit_types": ["credits", "presale_access", "fan_rewards"],
        "key_pages": [
            "/account-summary/",
            "/account/",
        ],
        "login_url": "https://auth.ticketmaster.com/as/authorization.oauth2",
    },
    "clear": {
        "display_name": "CLEAR",
        "domain": "clearme.com",
        "category": "travel",
        "benefit_types": ["membership_expiry", "family_accounts", "tier_status"],
        "key_pages": [
            "/account/",
            "/account/membership",
        ],
        "login_url": "https://www.clearme.com/account/",
    },
    "amtrak": {
        "display_name": "Amtrak Guest Rewards",
        "domain": "amtrak.com",
        "category": "travel",
        "benefit_types": ["points", "tier_status", "companion_coupon"],
        "key_pages": [
            "/my-account/",
            "/my-account/rewards",
        ],
        "login_url": "https://www.amtrak.com/account/login",
    },
    "global_entry": {
        "display_name": "Global Entry / TSA PreCheck",
        "domain": "ttp.dhs.gov",
        "category": "travel",
        "benefit_types": ["membership_expiry", "known_traveler_number"],
        "key_pages": ["/dashboard"],
        "login_url": "https://ttp.dhs.gov/",
    },

    # ── Utilities ─────────────────────────────────────────────────────────────
    "pa_utilities": {
        "display_name": "Palo Alto Utilities",
        "domain": "cityofpaloalto.org",
        "category": "utility",
        "benefit_types": ["usage_kwh", "billing_amount", "autopay_status"],
        "key_pages": ["/account/"],
        "login_url": "https://utilities.cityofpaloalto.org/",
    },
    "pg_and_e": {
        "display_name": "PG&E",
        "domain": "pge.com",
        "category": "utility",
        "benefit_types": ["usage_kwh", "billing_amount", "autopay_status", "net_energy_metering"],
        "key_pages": [
            "/MyEnergy/",
            "/MyAccount/",
        ],
        "login_url": "https://m.pge.com/#login",
    },
    "con_ed": {
        "display_name": "Con Edison",
        "domain": "coned.com",
        "category": "utility",
        "benefit_types": ["usage_kwh", "billing_amount", "autopay_status"],
        "key_pages": ["/account/"],
        "login_url": "https://www.coned.com/account",
    },
    "duke_energy": {
        "display_name": "Duke Energy",
        "domain": "duke-energy.com",
        "category": "utility",
        "benefit_types": ["usage_kwh", "billing_amount", "autopay_status"],
        "key_pages": ["/my-account/"],
        "login_url": "https://www.duke-energy.com/home/products/powerManager",
    },
}


# ---------------------------------------------------------------------------
# Source → expected URL hostname fragments for domain enforcement
# ---------------------------------------------------------------------------
SOURCE_DOMAINS: dict[str, list[str]] = {
    # Airlines
    "delta":             ["delta.com"],
    "united":            ["united.com"],
    "american_air":      ["aa.com", "americanairlines.com"],
    "southwest":         ["southwest.com"],
    "alaska_air":        ["alaskaair.com"],
    "jetblue":           ["jetblue.com"],
    "frontier":          ["flyfrontier.com"],
    "spirit":            ["spirit.com"],
    "air_canada":        ["aircanada.com"],
    # Hotels
    "marriott":          ["marriott.com", "bonvoy.marriott.com"],
    "hilton":            ["hilton.com"],
    "hyatt":             ["hyatt.com"],
    "ihg":               ["ihg.com"],
    "wyndham":           ["wyndham.com", "wyndhamhotels.com"],
    "choice_hotels":     ["choicehotels.com"],
    "best_western":      ["bestwestern.com"],
    "radisson":          ["radissonhotels.com", "radisson.com"],
    "mgm_rewards":       ["mgmresorts.com"],
    # Credit cards & banks
    "amex":              ["americanexpress.com"],
    "chase":             ["chase.com"],
    "citi":              ["citi.com", "citibank.com"],
    "capital_one":       ["capitalone.com"],
    "discover":          ["discover.com", "discovercard.com"],
    "wells_fargo":       ["wellsfargo.com"],
    "bofa":              ["bankofamerica.com"],
    "us_bank":           ["usbank.com"],
    "barclays":          ["barclaysus.com"],
    "paypal":            ["paypal.com"],
    "venmo":             ["venmo.com"],
    "fidelity":          ["fidelity.com"],
    "schwab":            ["schwab.com"],
    "vanguard":          ["vanguard.com"],
    "sofi":              ["sofi.com"],
    "sfcu":              ["sfcu.org"],
    # Telecom
    "xfinity":           ["xfinity.com", "comcast.com"],
    "att":               ["att.com"],
    "att_wireless":      ["att.com", "myatt.att.com"],
    "verizon":           ["verizon.com"],
    "tmobile":           ["t-mobile.com"],
    "spectrum":          ["spectrum.com"],
    "cox":               ["cox.com"],
    # Streaming
    "netflix":           ["netflix.com"],
    "disney_plus":       ["disneyplus.com"],
    "hulu":              ["hulu.com"],
    "spotify":           ["spotify.com"],
    "max":               ["max.com"],
    "peacock":           ["peacocktv.com"],
    "paramount_plus":    ["paramountplus.com"],
    "amazon_prime":      ["amazon.com"],
    "apple_tv":          ["tv.apple.com", "apple.com"],
    "youtube_premium":   ["youtube.com"],
    # Retail
    "amazon":            ["amazon.com"],
    "target":            ["target.com"],
    "walmart":           ["walmart.com"],
    "costco":            ["costco.com"],
    "starbucks":         ["starbucks.com"],
    "cvs":               ["cvs.com"],
    "walgreens":         ["walgreens.com"],
    "kroger":            ["kroger.com"],
    "sephora":           ["sephora.com"],
    "ulta":              ["ulta.com"],
    "nordstrom":         ["nordstrom.com"],
    "macys":             ["macys.com"],
    "kohls":             ["kohls.com"],
    "bestbuy":           ["bestbuy.com"],
    "homedepot":         ["homedepot.com"],
    "nike":              ["nike.com"],
    # Car rental
    "hertz":             ["hertz.com"],
    "avis":              ["avis.com"],
    "enterprise":        ["enterprise.com"],
    "national":          ["nationalcar.com"],
    "budget":            ["budget.com"],
    # Food & rideshare
    "uber":              ["uber.com"],
    "lyft":              ["lyft.com"],
    "doordash":          ["doordash.com"],
    "grubhub":           ["grubhub.com"],
    "chipotle":          ["chipotle.com"],
    "panera":            ["panerabread.com"],
    # Health
    "kaiser":            ["kp.org"],
    "aetna":             ["aetna.com"],
    "cigna":             ["cigna.com"],
    "united_health":     ["myuhc.com", "uhc.com"],
    "peloton":           ["onepeloton.com"],
    # Insurance
    "state_farm":        ["statefarm.com"],
    "geico":             ["geico.com"],
    "progressive":       ["progressive.com"],
    "allstate":          ["allstate.com"],
    "usaa":              ["usaa.com"],
    "aaa":               ["aaa.com"],
    # Travel
    "airbnb":            ["airbnb.com"],
    "expedia":           ["expedia.com"],
    "booking":           ["booking.com"],
    "ticketmaster":      ["ticketmaster.com"],
    "clear":             ["clearme.com"],
    "amtrak":            ["amtrak.com"],
    "global_entry":      ["ttp.dhs.gov"],
    # Utilities
    "pa_utilities":      ["cityofpaloalto.org", "utilities.cityofpaloalto.org"],
    "pg_and_e":          ["pge.com"],
    "con_ed":            ["coned.com"],
    "duke_energy":       ["duke-energy.com"],
}


def _url_allowed_for_source(source: str, url: str) -> bool:
    """Return True if the URL's hostname looks like it belongs to the given source."""
    if not url:
        return True  # no URL = locally generated, allow
    try:
        from urllib.parse import urlparse as _urlparse
        hostname = _urlparse(url).hostname or ""
    except Exception:
        return False
    # Block obviously bad URLs
    if not hostname or hostname in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False
    if hostname.startswith("192.168.") or hostname.startswith("10.") or hostname.startswith("172."):
        return False
    allowed = SOURCE_DOMAINS.get(source)
    if not allowed:
        return True  # unknown source — can't validate, allow through
    return any(hostname == d or hostname.endswith("." + d) for d in allowed)


# ---------------------------------------------------------------------------
# Canonical benefit type normalization → now lives in mighty/classify.py
# (classify_benefit() is imported at the top of this file)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Transfer partner graph
# ---------------------------------------------------------------------------

# Maps (source, points_field_fragment) → list of (dest_source, transfer_ratio)
# transfer_ratio: how many dest points per 1 source point
TRANSFER_PARTNERS: dict[str, list[tuple[str, float]]] = {
    # Amex Membership Rewards
    "amex:membership_rewards": [
        ("delta",    1.0),
        ("marriott", 1.0),
        ("hilton",   1.0),
        ("british_airways", 1.0),
        ("air_canada", 1.0),
        ("singapore", 1.0),
    ],
    # Chase Ultimate Rewards
    "chase:ultimate_rewards": [
        ("hyatt",      1.0),
        ("united",     1.0),
        ("marriott",   1.0),
        ("southwest",  1.0),
        ("british_airways", 1.0),
        ("singapore",  1.0),
    ],
    # Citi ThankYou Points
    "citi:thank_you": [
        ("american_air",   1.0),
        ("hilton",     1.0),
        ("singapore",  1.0),
        ("turkish",    1.0),
    ],
    # Capital One Miles (future)
    "capital_one:miles": [
        ("air_canada", 1.0),
        ("turkish",    1.0),
        ("avianca",    1.0),
    ],
}


def _get_transferable_points(uid: str, dest_source: str) -> list[dict]:
    """
    Returns a list of points balances from OTHER sources that can be
    transferred to dest_source, with estimated transfer amount.
    e.g. dest_source='hyatt' returns [{'source':'chase', 'label':'Ultimate Rewards', 'balance':45000}]
    """
    import json as _jtx
    import re as _re_tx
    results = []

    # Which partner keys target this dest?
    relevant_partners = [
        (pk, partners)
        for pk, partners in TRANSFER_PARTNERS.items()
        if any(dest == dest_source for dest, _ in partners)
    ]
    if not relevant_partners:
        return []

    # Scan account_data for source accounts
    rows = get_db().execute(
        "SELECT source, data_enc FROM account_data WHERE user_id=?", (uid,)
    ).fetchall()

    for row in rows:
        src = row["source"]
        for partner_key, partners in relevant_partners:
            pk_source = partner_key.split(":")[0]
            if src != pk_source:
                continue
            # Parse fields
            try:
                raw = decrypt_account_data(uid, row["data_enc"] or "")
                fields = raw.get("items") or raw.get("ai_items") or []
                if not isinstance(fields, list):
                    continue
            except Exception:
                continue
            for f in fields:
                if not isinstance(f, dict):
                    continue
                fk = (f.get("key") or "").lower()
                fv = str(f.get("value") or "")
                # Check if this field is a transferable points balance
                pk_field = partner_key.split(":")[1]
                if pk_field.replace("_", "") in fk.replace("_", ""):
                    nums = _re_tx.findall(r'[\d,]+', fv)
                    if nums:
                        balance = int(nums[0].replace(',', ''))
                        if balance > 1000:  # ignore trivial balances
                            results.append({
                                "source": src,
                                "label": f.get("label") or fk.replace("_", " ").title(),
                                "balance": balance,
                                "partner_key": partner_key,
                            })
    return results


# ---------------------------------------------------------------------------
# Cross-account opportunity generation
# ---------------------------------------------------------------------------

def _generate_opportunities(uid: str, context: str | None = None) -> list[dict]:
    """
    Scans all connected accounts and generates cross-account opportunity objects.
    Each opportunity groups related benefits from multiple sources.
    Returns list of opportunity dicts sorted by relevance.

    An opportunity looks like:
    {
        "id": "hotel_hyatt_20240618",
        "context": "hotel",
        "title": "Hyatt Stay",
        "components": [
            {"source": "hyatt",  "label": "Free Night Award",     "canonical": "FREE_NIGHT",  "value": "1 cert"},
            {"source": "chase",  "label": "Ultimate Rewards",     "canonical": "MILES_POINTS","value": "45,000 pts (transferable)"},
            {"source": "hyatt",  "label": "Globalist Status",     "canonical": "STATUS",      "value": "Globalist"},
        ],
        "urgency": "soon",       # urgent / soon / none
        "expires_label": "Cert expires in 24 days",
        "relevance_score": 0.87,
        "why": "Free Night cert expires soon. Chase points transfer 1:1 to Hyatt.",
    }
    """
    import json as _jop
    import re  as _rop
    import datetime as _dop

    # Context → which _type values (from mighty/classify.py) are relevant
    CONTEXT_TYPE_MAP: dict[str, list[str]] = {
        "hotel":    ["certificate", "points_balance", "elite_status", "cash_credit", "travel_credit"],
        "flight":   ["travel_credit", "certificate", "cash_credit", "points_balance", "elite_status"],
        "car":      ["cash_credit", "membership"],
        "shopping": ["cash_credit", "points_balance"],
        "dining":   ["cash_credit", "points_balance"],
    }
    all_types = list({t for ts in CONTEXT_TYPE_MAP.values() for t in ts})
    relevant_types = CONTEXT_TYPE_MAP.get(context or "", all_types)

    # Collect all benefits across all accounts
    all_benefits: list[dict] = []
    rows = get_db().execute(
        "SELECT source, data_enc FROM account_data WHERE user_id=?", (uid,)
    ).fetchall()

    for row in rows:
        src = row["source"]
        try:
            raw = decrypt_account_data(uid, row["data_enc"] or "")
            fields = raw.get("items") or raw.get("ai_items") or []
            if not isinstance(fields, list):
                fields = []
        except Exception:
            continue

        caps = SOURCE_CAPABILITIES.get(src, {})
        src_display = caps.get("display_name", src.title())

        for f in fields:
            if not isinstance(f, dict):
                continue
            fk  = (f.get("key") or "").lower()
            fl  = f.get("label") or fk.replace("_", " ").title()
            fv  = str(f.get("value") or "")
            fc  = float(f.get("confidence", 0.85))

            # ── Value-level pre-filters ──────────────────────────────────────
            # Skip account identifiers and metadata — not actionable
            _SKIP_KEY_FRAGMENTS = (
                "_number", "_id", "member_since", "account_number",
                "loyalty_number", "member_id", "account_id", "customer_id",
                "program_number", "joining_date", "since_", "enrollment",
                "username", "email", "phone", "address",
            )
            if any(skip in fk for skip in _SKIP_KEY_FRAGMENTS):
                continue

            # Skip empty or placeholder values
            _fv_stripped = fv.strip()
            if not _fv_stripped or _fv_stripped in {"—", "-", "–", "N/A", "n/a", "None", "0", "TBD", ""}:
                continue

            # Skip "0 of X" progress fields (user has made zero progress)
            if _rop.match(r'^0\s+of\s+[\d,]+', _fv_stripped):
                continue

            # Skip bare account/loyalty numbers (7+ digit strings)
            if _rop.match(r'^\d{7,}$', _fv_stripped.replace(",", "").replace(" ", "")):
                continue

            # Skip year-only values that look like join dates ("2002", "2018")
            if _rop.match(r'^(19|20)\d{2}$', _fv_stripped):
                continue
            # ────────────────────────────────────────────────────────────────

            btype = f.get("_type") or classify_benefit(fl, fv, src)
            if btype in ("other", "progress_toward", "expiry_date", "reservation"):
                continue
            if btype not in relevant_types:
                continue

            # Expiration parsing — try multiple formats
            exp_days = None
            exp_label = ""
            _combined_for_exp = f"{fl} {fv}"

            # Pattern 1: "X days" / "expires in X days"
            _m1 = _rop.search(r'(\d+)\s*day', _combined_for_exp, _rop.I)
            if _m1:
                exp_days = int(_m1.group(1))
                exp_label = f"Expires in {exp_days} days"

            # Pattern 2: MM/DD/YYYY or MM/DD/YY
            if exp_days is None:
                _m2 = _rop.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', _combined_for_exp)
                if _m2:
                    try:
                        _mo, _da, _yr = int(_m2.group(1)), int(_m2.group(2)), int(_m2.group(3))
                        if _yr < 100: _yr += 2000
                        _exp_date = _dop.date(_yr, _mo, _da)
                        exp_days = (_exp_date - _dop.date.today()).days
                        if exp_days >= 0:
                            exp_label = f"Expires {_exp_date.strftime('%b %-d, %Y')}"
                        else:
                            exp_days = None  # already expired, don't surface
                    except ValueError:
                        pass

            # Pattern 3: Month YYYY (e.g. "January 2027", "Jan 2027")
            if exp_days is None:
                _m3 = _rop.search(
                    r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{4})',
                    _combined_for_exp, _rop.I
                )
                if _m3:
                    _mon_map = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
                                'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
                    try:
                        _mo = _mon_map[_m3.group(1)[:3].lower()]
                        _yr = int(_m3.group(2))
                        _exp_date = _dop.date(_yr, _mo, 1)
                        exp_days = (_exp_date - _dop.date.today()).days
                        if exp_days >= 0:
                            exp_label = f"Expires {_exp_date.strftime('%b %Y')}"
                        else:
                            exp_days = None
                    except (ValueError, KeyError):
                        pass

            # Pattern 4: "exp MM/YY" (short credit card style)
            if exp_days is None:
                _m4 = _rop.search(r'\bexp\.?\s*(\d{1,2})/(\d{2,4})\b', _combined_for_exp, _rop.I)
                if _m4:
                    try:
                        _mo, _yr = int(_m4.group(1)), int(_m4.group(2))
                        if _yr < 100: _yr += 2000
                        _exp_date = _dop.date(_yr, _mo, 1)
                        exp_days = (_exp_date - _dop.date.today()).days
                        if exp_days >= 0:
                            exp_label = f"Expires {_exp_date.strftime('%m/%Y')}"
                        else:
                            exp_days = None
                    except ValueError:
                        pass

            urgency = "none"
            if exp_days is not None:
                if exp_days <= 14:    urgency = "urgent"
                elif exp_days <= 45:  urgency = "soon"

            score, factors = _relevance_score(fk, fl, fv, fc, context, None)

            all_benefits.append({
                "source":      src,
                "source_display": src_display,
                "field_key":   fk,
                "label":       fl,
                "value":       fv,
                "canonical":   btype,
                "urgency":     urgency,
                "exp_days":    exp_days,
                "exp_label":   exp_label,
                "confidence":  fc,
                "score":       score,
                "factors":     factors,
            })

    if not all_benefits:
        return []

    # Sort all benefits by score descending
    all_benefits.sort(key=lambda x: x["score"], reverse=True)

    # Group into opportunity clusters by (context + primary_source)
    # Strategy: anchor on the highest-scoring benefit per source, then
    # attach transferable points from other sources
    seen_keys: set[tuple] = set()
    opportunities: list[dict] = []

    for anchor in all_benefits:
        src = anchor["source"]
        if (src, anchor["field_key"]) in seen_keys:
            continue

        # Build components for this opportunity: start with anchor
        components = [anchor.copy()]
        seen_keys.add((src, anchor["field_key"]))

        # Add other top benefits from same source (complementary canonical types)
        for b in all_benefits:
            if b["source"] == src and b["field_key"] != anchor["field_key"]:
                key = (b["source"], b["field_key"])
                if key not in seen_keys and b["canonical"] != anchor["canonical"]:
                    components.append(b.copy())
                    seen_keys.add(key)
                if len(components) >= 3:
                    break

        # Check for transferable points from other sources
        transferable = _get_transferable_points(uid, src)
        for tx in transferable:
            # Transferable points can complement multiple opportunities — don't lock them to one
            components.append({
                "source":         tx["source"],
                "source_display": SOURCE_CAPABILITIES.get(tx["source"], {}).get("display_name", tx["source"].title()),
                "label":          tx["label"] + " (transferable)",
                "value":          f"{tx['balance']:,} pts",
                "canonical":      "MILES_POINTS",
                "urgency":        "none",
                "exp_label":      "",
                "confidence":     0.9,
                "score":          0.4,
                "factors":        {},
                "is_transfer":    True,
            })

        if len(components) < 1:
            continue

        # Skip STATUS-only cards — elite status is something you are, not something you redeem
        _non_status = [c for c in components if c.get("canonical") != "STATUS"]
        if not _non_status:
            continue

        # Skip lone points balances with no expiry — a balance is account data, not an insight.
        # Opportunities need either urgency, or multiple actionable components, or a cross-account angle.
        _non_transfer = [c for c in components if not c.get("is_transfer")]
        _actionable_canonicals = {"FREE_NIGHT", "FLIGHT_CREDIT", "COMPANION_CERT",
                                  "UPGRADE_CERT", "STATEMENT_CREDIT", "PURCHASE_PROTECTION",
                                  "TRIP_PROTECTION"}
        _has_actionable = any(c.get("canonical") in _actionable_canonicals for c in _non_transfer)
        _has_urgency    = any(c.get("urgency") in ("urgent", "soon") for c in components)
        _is_solo_points = (
            len(_non_transfer) == 1
            and _non_transfer[0].get("canonical") == "MILES_POINTS"
            and not _has_urgency
            and not _has_actionable
        )
        if _is_solo_points:
            continue

        # Compute opportunity-level urgency and score
        opp_urgency = "none"
        for c in components:
            if c.get("urgency") == "urgent":   opp_urgency = "urgent";  break
            if c.get("urgency") == "soon":      opp_urgency = "soon"
        opp_score = max(c["score"] for c in components)

        expires_labels = [c["exp_label"] for c in components if c.get("exp_label")]
        exp_label_str = expires_labels[0] if expires_labels else ""

        # Build "why" explanation
        why_parts = []
        for c in components:
            if c.get("exp_label"):
                why_parts.append(f"{c['label']} {c['exp_label'].lower()}")
        if context:
            why_parts.insert(0, f"Relevant for {context} booking")
        # Type-specific fallback why text — better than "Available in your accounts"
        if not why_parts:
            _canonical_set = {c.get("canonical") for c in components}
            if "FREE_NIGHT" in _canonical_set:
                why_parts = ["Free night cert available to book"]
            elif "FLIGHT_CREDIT" in _canonical_set:
                why_parts = ["Flight credit can be applied at checkout"]
            elif "COMPANION_CERT" in _canonical_set:
                why_parts = ["Companion certificate can bring a second passenger free"]
            elif "UPGRADE_CERT" in _canonical_set:
                why_parts = ["Upgrade certificate available — use when booking or at the gate"]
            elif "MILES_POINTS" in _canonical_set:
                caps_name_why = SOURCE_CAPABILITIES.get(src, {}).get("display_name", src.title())
                why_parts = [f"Redeem your {caps_name_why} balance for award travel"]
            elif "STATEMENT_CREDIT" in _canonical_set:
                why_parts = ["Statement credit available — use it before it resets"]
            else:
                why_parts = [f"Benefit available in your {SOURCE_CAPABILITIES.get(src, {}).get('display_name', src.title())} account"]
        why_str = ". ".join(why_parts[:3]) + "."

        # Title: describe the insight, not the category it came from
        # "Southwest — 24,617 Rapid Rewards" is more useful than "Southwest Opportunity"
        caps_name = SOURCE_CAPABILITIES.get(src, {}).get("display_name", src.title())
        short_name = caps_name.split()[0]  # "Marriott" from "Marriott Bonvoy"
        ctx_noun = {"hotel": "Stay", "flight": "Flight", "car": "Rental",
                    "shopping": "Purchase", "dining": "Meal"}.get(context or "", "")
        # Pick the anchor component's value for the title if short and meaningful
        _anchor = components[0]
        _anchor_val = _anchor.get("value", "")
        _anchor_label = _anchor.get("label", "")
        if ctx_noun:
            title = f"{short_name} {ctx_noun}"
        elif _anchor_val and len(_anchor_val) <= 20 and not _anchor_val.startswith("0") \
                and _anchor.get("canonical") != "MILES_POINTS":
            title = f"{short_name} — {_anchor_val} {_anchor_label}"[:60]
        else:
            title = f"{short_name} — {_anchor_label}"[:60]

        opportunities.append({
            "id":           f"{context}_{src}_{_dop.date.today().isoformat()}",
            "context":      context,
            "title":        title,
            "source":       src,
            "components":   [
                {
                    "source":         c["source"],
                    "source_display": c.get("source_display", ""),
                    "label":          c["label"],
                    "value":          c["value"],
                    "canonical":      c["canonical"],
                    "exp_label":      c.get("exp_label", ""),
                    "is_transfer":    c.get("is_transfer", False),
                }
                for c in components
            ],
            "urgency":        opp_urgency,
            "expires_label":  exp_label_str,
            "relevance_score": round(opp_score, 3),
            "why":            why_str,
        })

        if len(opportunities) >= 6:
            break

    return opportunities


def _get_missing_benefits(uid: str) -> list[dict]:
    """
    Returns a list of benefit types that SOURCE_CAPABILITIES says should exist
    for a connected source, but haven't been found yet in account_data.
    Each item: {source, display_name, missing_type, key_pages, message}
    """
    import json as _jmb
    missing = []
    rows = get_db().execute(
        "SELECT source, data_enc FROM account_data WHERE user_id=?", (uid,)
    ).fetchall()

    for row in rows:
        src = row["source"]
        caps = SOURCE_CAPABILITIES.get(src)
        if not caps:
            continue

        # Decrypt and parse
        try:
            data = decrypt_account_data(uid, row["data_enc"] or "")
            items_list = data.get("items", []) or data.get("ai_items", []) or []
            if not isinstance(items_list, list):
                items_list = []
        except Exception:
            continue

        # Collect found field keys
        found_keys = set()
        for f in items_list:
            if isinstance(f, dict):
                k = (f.get("key") or f.get("field_key") or "").lower()
                found_keys.add(k)

        # Check which benefit types are expected but not found
        for btype in caps["benefit_types"]:
            # Check if any found key contains the benefit type (or close fragment)
            fragments = btype.replace("_", " ").split()
            if not any(frag in fk for frag in fragments for fk in found_keys):
                missing.append({
                    "source": src,
                    "display_name": caps["display_name"],
                    "missing_type": btype,
                    "key_pages": caps.get("key_pages", []),
                    "message": f"{caps['display_name']} — {btype.replace('_',' ')} not yet found",
                })

    # Cap: return top 5 by source diversity (one per source)
    seen_sources = set()
    top = []
    for m in missing:
        if m["source"] not in seen_sources:
            top.append(m)
            seen_sources.add(m["source"])
        if len(top) >= 5:
            break
    return top


# Maps expected field keys → URL path keywords likely to contain them
# Used for goal-driven crawl: "certificates missing → look for pages with these keywords"
_FIELD_TO_PATH_KEYWORDS: dict[str, list[str]] = {
    "certificates":    ["certificate", "cert", "award", "companion", "free-night", "fnc"],
    "travel_credits":  ["credits", "wallet", "ecredit", "voucher", "travel-credit", "benefits"],
    "upgrades":        ["upgrade", "sticker", "gpu", "rpu", "instrument", "systemwide"],
    "upcoming_trips":  ["trips", "travel", "itinerary", "reservation", "booking", "upcoming"],
    "elite_status":    ["status", "medallion", "loyalty", "tier", "elite"],
    "miles_balance":   ["skymiles", "miles", "points", "balance", "mileageplus", "rapid-rewards"],
    "statement_credits": ["credits", "benefits", "rewards", "statement", "offers"],
    "current_balance": ["account", "billing", "statement", "summary", "balance"],
    "payment_due_date":["billing", "payment", "due", "invoice"],
    "policy_number":   ["policy", "coverage", "plan", "details"],
    "premium":         ["billing", "payment", "premium", "invoice"],
    "amount_due":      ["billing", "payment", "account", "invoice", "summary"],
    "checking_balance":["account", "checking", "dashboard", "overview"],
    "savings_balance": ["savings", "account", "dashboard", "overview"],
    "renewal_date":    ["membership", "account", "plan", "subscription", "renewal"],
}

# Inference rules: if a field VALUE matches these patterns, expect additional fields
# Format: (field_key, value_substring) → [additional_expected_field_keys]
_INFERENCE_RULES: list[tuple[str, str, list[str]]] = [
    # Companion pass existence guarantees a points balance exists
    ("companion_pass",   "active",    ["miles_balance"]),
    ("companion_pass",   "available", ["miles_balance"]),
    # Free night cert existence implies a points balance (hotel loyalty)
    ("free_night",       "",          ["miles_balance"]),
    ("certificate_type", "free night",["miles_balance"]),
    # Annual fee implies renewal date exists
    ("annual_fee",       "",          ["renewal_date"]),
    # If auto-pay found, due date and balance usually exist too
    ("auto_pay",         "enabled",   ["amount_due", "due_date"]),
    ("auto_pay_status",  "enrolled",  ["amount_due", "due_date"]),
]

def _apply_inference_rules(found_fields: list[dict]) -> list[str]:
    """Given found fields, return additional expected field keys implied by those values."""
    additional = set()
    for f in found_fields:
        fk = f.get("key", "").lower().replace("-", "_")
        fv = f.get("value", "").lower()
        if not fv:
            continue  # skip fields with no value
        for rule_key, rule_val, implied in _INFERENCE_RULES:
            rule_key_norm = rule_key.replace("-", "_")
            if rule_key_norm not in fk:
                continue
            if rule_val and rule_val not in fv:
                continue  # value must contain the trigger substring if specified
            additional.update(implied)
    return list(additional)


# mighty/ package lives alongside app.py in the repo root. If the package is missing
# (e.g. partial deploy), we fall back to inline stubs so the app still starts.
try:
    from mighty.classify import (classify_benefit, BENEFIT_TYPES,         # noqa: E402
                                  is_actionable, is_balance, is_status,
                                  is_needs_attention, is_upcoming)
    from mighty.scoring import (_relevance_score, _confidence_label,          # noqa: E402
                                _BENEFIT_APPLICABILITY,
                                score_opportunity, urgency_from_score)
except ImportError:
    import re as _cb_re
    _BT_RULES_INLINE = [
        ("progress_toward", ["progress", "of ", "earned", "qualifying", "toward"],
         [r"\d+\s*(?:of|/)\s*\d+", r"\d+%"], []),
        ("elite_status",    ["status", "tier", "medallion", "elite", "platinum", "gold", "diamond", "premier", "globalist"],
         [], ["autopay", "payment", "bill", "subscription", "enrolled", "enabled", "active", "loyalty number", "member id"]),
        ("certificate",     ["certificate", "cert", "companion", "free night", "award night", "upgrade cert", "buddy pass"],
         [], ["progress", "of "]),
        ("cash_credit",     ["annual credit", "annual fee credit", "cash back", "statement credit", "dining credit", "hotel credit"],
         [r"\$\d"], ["ecredit", "flight credit", "travel credit"]),
        ("travel_credit",   ["travel credit", "flight credit", "ecredit", "trip credit", "airline credit"], [r"\$\d"], []),
        ("membership",      ["membership", "lounge", "global entry", "clear", "tsa pre"], [], []),
        ("points_balance",  ["points", "miles", "rewards", "skymiles", "bonvoy", "honors"], [r"\d{3,}"], []),
        ("elite_status",    ["status", "tier"], [], []),
    ]
    def classify_benefit(label: str, value: str, source: str = "") -> str:
        combined = (label + " " + value).lower()
        lbl = label.lower(); val = value.lower()
        for btype, lkws, vpats, excl in _BT_RULES_INLINE:
            if any(e in combined for e in excl): continue
            if any(k in lbl for k in lkws) or any(_cb_re.search(p, val) for p in vpats):
                return btype
        return "other"
    BENEFIT_TYPES = ["elite_status","certificate","travel_credit","cash_credit",
                     "points_balance","progress_toward","membership","reservation",
                     "payment_due","renewal","partner_benefit","expiry_date","other"]
    def is_actionable(btype): return btype in {"certificate","travel_credit","cash_credit"}
    def is_balance(btype):    return btype == "points_balance"
    def is_status(btype):     return btype == "elite_status"
    def is_needs_attention(btype): return btype in {"payment_due","renewal"}
    def is_upcoming(btype):   return btype in {"upcoming_event","reservation"}
    _BENEFIT_APPLICABILITY: dict = {
        "flight":   ["companion","ecredit","miles","travel_credit","upgrade","certificate"],
        "hotel":    ["free_night","award_night","points","hotel_credit","travel_credit","certificate"],
        "car":      ["rental","insurance","coverage"],
        "shopping": ["cash_back","credit"],
        "dining":   ["dining_credit","cash_back","credit"],
    }
    def _relevance_score(field_key="", field_label="", field_value="",  # type: ignore[misc]
                         confidence=0.85, context=None, expiry_date_str=None):
        return 0.5, {}
    def score_opportunity(item, user_intent=None, source="", user_type_affinity=None):  # type: ignore[misc]
        d = item.get("days_left")
        if d is not None and d <= 7:  return 70
        if d is not None and d <= 30: return 45
        if d is not None and d <= 60: return 35
        return 20
    def urgency_from_score(score):  # type: ignore[misc]
        if score >= 65: return "urgent"
        if score >= 38: return "soon"
        return "info"
    def _confidence_label(score: float) -> str:
        return "High" if score >= 0.85 else "Medium" if score >= 0.60 else "Needs review"
    print("[Mighty] WARNING: mighty/ package not found — using inline fallbacks", flush=True)

# Partnership graph — cross-program perks + card recommendations
try:
    from mighty.partnerships import get_derived_benefits, get_card_recommendations  # noqa: E402
except ImportError:
    def get_derived_benefits(items, connected_sources, context):  # type: ignore[misc]
        return []
    def get_card_recommendations(connected_sources, context=None):  # type: ignore[misc]
        return []
    print("[Mighty] WARNING: mighty/partnerships.py not found — derived benefits disabled", flush=True)

def _post_filter_fields(fields: list, source: str = "") -> list:
    """Remove fields that are clearly noise regardless of what the AI returned.

    Applied after Gemini responds so prompt language alone can't be gamed.
    """
    import datetime as _dt, re as _re

    _today = _dt.date.today()

    # Labels that are pure noise (raw identifiers, not useful context)
    _LABEL_NOISE = (
        "eticket", "e-ticket", "ticket number", "confirmation number",
        "loyalty id", "member id", "membership id", "account number",
        "record locator", "pnr",
        "honors number", "honors #", "member number", "rewards number",
        "membership number", "frequent flyer number", "ff number",
    )

    # Patterns for values that are pure long numeric IDs (>12 consecutive digits)
    _LONG_NUM_RE = _re.compile(r'^\d{12,}$')

    def _is_past_date(value: str) -> bool:
        """Return True if value is a date string that is in the past.

        Normalises month spellings (Sept → Sep, August → Aug) before parsing
        so strptime's %b can match written-out forms from Gemini's output.
        """
        norm = _normalise_date_str(value)
        for fmt in (
            "%Y-%m-%d",           # 2026-08-14
            "%m/%d/%Y",           # 08/14/2026
            "%b %d, %Y",          # Aug 14, 2026
            "%b %d %Y",           # Aug 14 2026  (no comma)
            "%B %d, %Y",          # August 14, 2026
            "%B %d %Y",           # August 14 2026
            "%d %b %Y",           # 14 Aug 2026
            "%d %B %Y",           # 14 August 2026
            "%Y/%m/%d",           # 2026/08/14
            "%d%b%Y",             # 22JUL2024
            "%d%B%Y",             # 22JULY2024
        ):
            for candidate in (norm, norm.upper()):
                try:
                    d = _dt.datetime.strptime(candidate.strip(), fmt).date()
                    return d < _today
                except ValueError:
                    pass
        return False

    # Labels that always mean "upcoming booking" — drop regardless of date format
    _UPCOMING_BOOKING_LABELS = (
        "upcoming flight", "upcoming reservation", "upcoming trip",
        "upcoming stay", "upcoming booking", "upcoming itinerary",
        "upcoming travel",
    )

    _BOOKING_TERMS = ("flight", "reservation", "booking", "trip",
                      "check-in", "check-out", "arrival", "departure",
                      "stay", "itinerary", "travel")

    # Regex to extract dates — ISO, slash, compact (22JUL2024), and written-out
    # month-name forms like "Aug 14, 2026" or "September 5 2026".
    _DATE_RE = _re.compile(
        r'\b(\d{4}-\d{2}-\d{2}'                                   # 2026-08-14
        r'|\d{1,2}/\d{1,2}/\d{4}'                                 # 08/14/2026
        r'|\d{1,2}[A-Za-z]{3}\d{4}'                               # 22JUL2024
        r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}'  # Aug 14, 2026
        r')\b',
        _re.IGNORECASE,
    )

    def _normalise_date_str(s: str) -> str:
        """Normalise written-out month abbreviations before strptime.
        Handles 'Sept' → 'Sep', 'June' → 'Jun', etc. so %b can parse them."""
        _month_map = {
            "january": "Jan", "february": "Feb", "march": "Mar", "april": "Apr",
            "may": "May", "june": "Jun", "july": "Jul", "august": "Aug",
            "september": "Sep", "sept": "Sep", "october": "Oct",
            "november": "Nov", "december": "Dec",
        }
        norm = s.strip()
        for long, short in _month_map.items():
            norm = _re.sub(long, short, norm, flags=_re.IGNORECASE)
        return norm

    # Values that are pure noise regardless of label
    _EMPTY_VALUES = frozenset({
        "", "0", "-", "–", "—", "n/a", "none", "na", "n.a.",
        "$0", "$0.0", "$0.00", "0.00", "no", "not available",
        "no match found", "tbd",
    })
    # "Pending" is noise only when it's a placeholder for a missing number/date,
    # not when the label describes a real status (claim, application, auth, etc.)
    _PENDING_STATUS_LABELS = (
        "status", "claim", "application", "approval", "auth", "verification",
        "request", "dispute", "refund", "transfer",
    )
    # Generic tier labels that carry no meaningful information
    _GENERIC_TIER_VALUES = frozenset({
        "cardmember", "member", "basic", "standard", "registered",
        "classic", "general", "associate",
    })
    # Login-wall substrings
    _LOGIN_WALL = ("log in", "sign in", "login to view", "sign in to see",
                   "login to see", "sign in to view", "please log in",
                   "please sign in")

    out = []
    for f in fields:
        label = (f.get("label") or "").strip()
        value = str(f.get("value") or "").strip()
        lbl_low = label.lower()
        val_low = value.lower()

        # Drop empty or explicitly null/zero values
        if val_low in _EMPTY_VALUES:
            continue

        # "Pending" is noise as a placeholder value (e.g. "Points Balance: Pending")
        # but not when the label describes a real status field.
        if val_low == "pending":
            if not any(sl in lbl_low for sl in _PENDING_STATUS_LABELS):
                continue

        # Drop login-wall values
        if any(lw in val_low for lw in _LOGIN_WALL):
            continue

        # Drop generic tier labels that tell the user nothing meaningful
        if val_low in _GENERIC_TIER_VALUES:
            continue

        # Drop loyalty-program hallucinations on utility/telecom sources
        _UTILITY_SOURCES = {
            "xfinity", "comcast", "spectrum", "cox", "centurylink", "att_internet",
            "pge", "sdge", "palo_alto_utilities", "verizon", "tmobile",
        }
        _LOYALTY_LABELS = {
            "elite status", "elite_status", "member status", "tier status", "medallion",
            "elite member", "diamond member", "platinum member", "gold member", "silver member",
            "loyalty tier", "membership tier", "reward tier", "status level",
        }
        _LOYALTY_VALUES = {
            "diamond member", "gold member", "platinum member", "silver member",
            "diamond", "platinum elite", "gold elite", "silver elite",
            "mosaic", "executive platinum", "1k", "global services",
        }
        if source in _UTILITY_SOURCES:
            if any(lbl in lbl_low or lbl in (f.get("key") or "").lower() for lbl in _LOYALTY_LABELS):
                continue
            if val_low in _LOYALTY_VALUES:
                continue

        # Drop labels that match known noise patterns
        if any(n in lbl_low for n in _LABEL_NOISE):
            continue

        # Drop fields whose value is only a long numeric ID
        if _LONG_NUM_RE.match(value.replace(" ", "").replace("-", "")):
            continue

        # Drop fields where the label contains a number that looks like a ticket ID
        # e.g. "eTicket 0062253264364 Expiry"
        if _re.search(r'\b\d{8,}\b', label):
            continue

        # Drop "upcoming X" labeled fields only if date is past or absent.
        # Future-dated trips are genuine upcoming events and must survive.
        if any(u in lbl_low for u in _UPCOMING_BOOKING_LABELS):
            _date_m = _DATE_RE.search(value + " " + label)
            if not _date_m or _is_past_date(_date_m.group(0)):
                continue
            # Future date — fall through and keep the field

        # Drop booking/travel fields where the embedded date is in the past
        # Supports standard formats AND compact forms like "22JUL2024"
        _date_match = _DATE_RE.search(value + " " + label)
        if _date_match and _is_past_date(_date_match.group(0)):
            if any(t in lbl_low for t in _BOOKING_TERMS):
                continue

        out.append(f)
    return out


DISCOVER_PROMPT = """You are analyzing one or more pages from a user's {site} account.
Pages may be separated by === URL === markers.
Today's date: {today}.
{category_hint}
CRITICAL — How to read structured data blocks:
=== EMBEDDED STATE === blocks contain JSON serialized directly into the page by the framework
(Next.js __NEXT_DATA__, Apollo __APOLLO_STATE__, Redux, etc.) before any API call fires.
This is the HIGHEST CONFIDENCE source — treat non-empty values here as ground truth.

=== API RESPONSE === blocks contain JSON from a specific network API call.
These are high-confidence but may be INCOMPLETE — a single API call often returns partial data.
If an API block shows empty arrays ([]) or null/empty for a field, that does NOT mean the user
has no data — the site loads different data from different endpoints.

In both cases: if the block shows empty/null but the page text clearly displays a value
(e.g. "Gold", "143,996 Points"), ALWAYS trust the displayed page text. A site may render
status from a different API call than the one captured. Never let an empty JSON field suppress
a value that is clearly visible on the page.

Page text:
{text}

Extract ONLY data that is SPECIFIC TO THIS USER's account — personalized numbers, statuses, dates, and benefits.

INCLUDE:
- Loyalty/rewards points, miles, or cash-back balance totals
- Tier or status level (Gold, Platinum, Diamond, A-List, etc.) — only meaningful named tiers, not generic labels like "Cardmember" or "Member"
- Progress toward next tier or status goal (e.g. "4 of 20 flights to A-List")
- Benefits the user CAN USE RIGHT NOW: certificates, upgrade awards, free nights, companion passes, lounge visits, travel credits, fee waivers — include the count or value and the expiry date if shown
- Expiration dates for points, status, or any benefit (even if the benefit itself is listed above)
- UPCOMING reservations or bookings — ONLY those with a future date (after today). If the trip date is in the past, REJECT it.
- Payment info: current balance owed, minimum payment due, payment due date, whether autopay is active, last payment received (date + amount), any past-due or overdue amount
- Personalized special offers with a specific deadline date AND a specific reward amount (e.g. "Earn 5,000 bonus points if you stay by Aug 31")

HARD EXCLUDE — never include these even if they appear on the page:
- Any value containing "log in", "sign in", "login to view", "sign in to see"
- Search or booking form fields (departure city, destination, travel dates, passenger count, cabin class)
- Site-wide availability windows (e.g. "Book travel through [date]", "Reservations Through: March 2027")
- "No match found", "None", "N/A", "–", empty values, or zero values ("0", "$0", "$0.00")
- Navigation labels, menu items, links, tab names, page headings with no data value
- Generic account-type labels that carry no meaningful tier information: "Cardmember", "Member", "Basic", "Standard", "Registered" — these tell the user nothing they don't already know
- PAST reservations, trips, or flights whose date has already occurred (before today) — these are history, not upcoming
- Generic marketing copy available to ALL users with no personalized quantity, deadline, or condition
- Contact and personal info: email addresses, phone numbers, mailing addresses, passport numbers — never useful on a dashboard
- Promotional offers with no specific personalized deadline AND no specific personalized reward quantity (if both are missing, REJECT)

CONCRETE REJECT EXAMPLES:
- "Points Balance Alert: Log in to view points balance" → REJECT (login wall)
- "Reservations Through: March 10, 2027" → REJECT (site-wide booking window, not a user reservation)
- "Depart Date: Fri, Jun 12, 2026" from a search widget → REJECT (search form)
- "Upcoming Flight: Jul 22, 2024" → REJECT (date is in the past)
- "Cardmember Status: Cardmember" → REJECT (generic label, tells the user nothing)
- "Membership Level: Member" → REJECT (redundant, not a meaningful tier)
- "Upcoming Trips: None" → REJECT (empty value)
- "Earn more points with our partners" → REJECT (generic marketing, no personalized amount or deadline)
- "Gift Cards Balance: 0" → REJECT (zero value)
- "Nights This Year: 0" → REJECT (zero value)
- "Primary Email Address: user@example.com" → REJECT (contact info)
- "Earn Up to 700 Points with Hertz" → REJECT (generic partner promotion, no personalized deadline or quantity)
- "Earn 2,000 Bonus Points Every Night" → REJECT (generic promotion, not a personalized offer)

CONCRETE INCLUDE EXAMPLES:
- "Gold Medallion" status → INCLUDE as {{"key":"elite_status","label":"Elite Status","value":"Gold Medallion","confidence":0.99,"source_snippet":"SkyMiles Gold Medallion status"}}
- "Gold" tier displayed prominently on account home page (e.g. "Gold\\nWelcome back, Jonathan") → INCLUDE as {{"key":"elite_status","label":"Elite Status","value":"Gold","confidence":0.97,"source_snippet":"Gold Welcome back"}}
- "24,617 Rapid Rewards points" → INCLUDE as {{"key":"rapid_rewards_points","label":"Rapid Rewards Points","value":"24,617","confidence":0.97,"source_snippet":"Rapid Rewards Points Balance 24,617"}}
- "0 of 20 flights" in A-List section → INCLUDE as {{"key":"alist_progress","label":"A-List Flights Progress","value":"0 of 20","confidence":0.93,"source_snippet":"A-List status 0 of 20 qualifying flights"}}
- "$2,472.20 Total Payment Due" → INCLUDE as {{"key":"balance_due","label":"Balance Due","value":"$2,472.20","confidence":0.99,"source_snippet":"Total Payment Due $2,472.20"}}
- "Minimum Payment Due: $35 by Jul 12, 2026" → INCLUDE as {{"key":"min_payment_due","label":"Minimum Payment Due","value":"$35 by Jul 12, 2026","confidence":0.98,"source_snippet":"Minimum Payment Due $35 by Jul 12, 2026"}}
- "Past Due Amount: $150" → INCLUDE as {{"key":"past_due_amount","label":"Past Due Amount","value":"$150","confidence":0.99,"source_snippet":"Past Due Amount $150"}}
- "Last payment: $2,472.20 received Jun 11, 2026" → INCLUDE as {{"key":"last_payment","label":"Last Payment Received","value":"$2,472.20 on Jun 11, 2026","confidence":0.97,"source_snippet":"last payment $2,472.20 received Jun 11"}}
- "AutoPay: Enrolled" → INCLUDE as {{"key":"autopay_status","label":"Auto Pay Status","value":"Enrolled","confidence":0.98,"source_snippet":"AutoPay Enrolled"}}
- "Free Night Award — expires Dec 31, 2026" → INCLUDE as {{"key":"free_night_award","label":"Free Night Award Expiry","value":"Dec 31, 2026","confidence":0.96,"source_snippet":"Free Night Award expires Dec 31, 2026"}}
- "2 Suite Night Awards available" → INCLUDE as {{"key":"suite_night_awards","label":"Suite Night Awards","value":"2 available","confidence":0.95,"source_snippet":"2 Suite Night Awards available"}}
- "Annual travel credit: $187 remaining" → INCLUDE as {{"key":"travel_credit_remaining","label":"Travel Credit Remaining","value":"$187","confidence":0.94,"source_snippet":"Annual travel credit $187 remaining"}}
- "Earn 5,000 bonus miles — book by Jul 15" → INCLUDE as {{"key":"bonus_miles_offer","label":"Bonus Miles Offer Deadline","value":"Jul 15, 2026","confidence":0.92,"source_snippet":"Earn 5,000 bonus miles book by Jul 15"}}
- "Global Upgrade Certificate — 1 available, expires Dec 31, 2026" → INCLUDE as {{"key":"upgrade_certificates","label":"Global Upgrade Certificates","value":"1 (exp Dec 31, 2026)","confidence":0.97,"source_snippet":"Global Upgrade Certificate 1 available expires Dec 31"}}
- "Companion Certificate — valid through Jan 15, 2027" → INCLUDE as {{"key":"companion_certificate","label":"Companion Certificate","value":"Valid through Jan 15, 2027","confidence":0.98,"source_snippet":"Companion Certificate valid through Jan 15, 2027"}}
- "Regional Upgrade Certificates: 4 available" → INCLUDE as {{"key":"regional_upgrade_certs","label":"Regional Upgrade Certificates","value":"4 available","confidence":0.97,"source_snippet":"Regional Upgrade Certificates 4 available"}}
- "Priority Pass membership — unlimited lounge visits" → INCLUDE as {{"key":"priority_pass","label":"Priority Pass Lounge Access","value":"Unlimited visits","confidence":0.90,"source_snippet":"Priority Pass membership unlimited lounge visits"}}
- "Free checked bag on all Delta flights" → INCLUDE as {{"key":"free_checked_bag","label":"Free Checked Bag Benefit","value":"All Delta flights","confidence":0.89,"source_snippet":"Free checked bag on all Delta flights"}}
- "Upcoming flight: SFO → JFK, Aug 14, 2026" → INCLUDE as {{"key":"upcoming_flight","label":"Upcoming Flight","value":"SFO → JFK, Aug 14, 2026","confidence":0.98,"source_snippet":"Upcoming flight SFO to JFK Aug 14 2026"}}

LABELING: write labels that make sense without knowing the site (no abbreviations, no page jargon). Labels should say what the value IS, not repeat the site name.

Return ONLY a JSON array, no other text:
[{{"key":"rapid_rewards_points","label":"Rapid Rewards Points","value":"24,617","confidence":0.97,"source_snippet":"Rapid Rewards Points Balance 24,617"}}]

Rules:
- key: snake_case, 1-4 words
- label: 2-5 words, self-explanatory out of context
- value: exact current value — if empty, zero, or a login prompt, skip the field entirely
- confidence: float 0.0–1.0 — how certain you are this is a real personalized user fact (not generic copy). Aim for >0.85 on solid data; below 0.70 means you are guessing.
- source_snippet: verbatim excerpt (≤15 words) from the page text that most directly supports this value
- Each concept ONCE, no duplicates
- Max 15 fields
- If you find zero fields that pass the hard-exclude test, return an empty array []

ORDERING — sort fields in this exact priority order (most important first):
1. Account status or tier (Gold, Platinum, Diamond, A-List — only meaningful named tiers)
2. Primary balance, points, or miles total
3. Available benefits (certificates, credits, awards with quantities)
4. Expiration dates for benefits, points, or status
5. Progress toward next tier goal
6. Upcoming reservations or bookings (future dates only)
7. Payment info (balance due, due date, past-due amount, autopay status)
8. Account metadata (member since, loyalty ID/member number — these go LAST)

The FIRST field in the array becomes the hero display — make it the single most meaningful thing about this account.
CRITICAL: Member numbers, account IDs, and loyalty IDs must NEVER be first. Status tier or primary balance must always lead.
CRITICAL: Generic account labels ("Cardmember", "Member") must NEVER be included — only include named tiers with real meaning.
CRITICAL: Past reservations (date already occurred) must NEVER be included as "upcoming"."""


import hashlib as _hashlib
import time as _time
_discovery_cache: dict[str, tuple[float, list]] = {}  # content-hash -> (timestamp, results)


def claude_discover_fields(raw_text: str, site_name: str, source: str | None = None) -> list:
    """Use Gemini Flash to identify all useful data fields in a page.

    Args:
        raw_text:  Full page text (or multi-page blob separated by === URL === markers).
        site_name: Human-readable site label (e.g. "Delta Air Lines").
        source:    Account source key (e.g. "delta") used to look up the category schema.
                   If provided, the prompt gets a focused hint on which field types to prioritise.
    """
    global _discovery_cache
    # Cache key: hash of first 5000 chars of raw_text + source
    cache_key = _hashlib.md5(((raw_text or "")[:5000] + str(source)).encode()).hexdigest()
    cached = _discovery_cache.get(cache_key)
    if cached and (_time.time() - cached[0]) < 60:
        return cached[1]  # return cached result

    if not _claude or not raw_text:
        return []
    try:
        # ── Extraction hints — load known trigger phrases for this site ─────────
        # extraction_hints records phrases that previously yielded high-confidence
        # fields. We pass them to _extract_candidate_snippets so those blocks are
        # force-included and boosted in the scorer, even if they lack generic triggers.
        _hint_phrases: list[str] = []
        if source:
            try:
                _hint_rows = get_db().execute(
                    "SELECT trigger_phrase FROM extraction_hints WHERE site=? "
                    "ORDER BY success_count DESC, confidence DESC LIMIT 50",
                    (source,)
                ).fetchall()
                _hint_phrases = [r["trigger_phrase"] for r in _hint_rows]
            except Exception:
                pass

        # ── Candidate snippet extraction ───────────────────────────────────────
        # Replace the raw page blob with focused windows around trigger words.
        # Falls back to raw_text[:8000] if no triggers match.
        snippets = _extract_candidate_snippets(raw_text, hint_phrases=_hint_phrases)
        print(
            f"[Mighty] Discovering fields for {site_name} (raw={len(raw_text)} chars, "
            f"snippets={len(snippets)} chars). Preview: {raw_text[:300]!r}",
            flush=True,
        )

        # ── Category hint ──────────────────────────────────────────────────────
        schema = _get_category_schema(source or "")
        if schema:
            category_hint = (
                f"\nThis is a {schema['name']}. "
                f"Prioritise these field types:\n  {schema['priority_fields']}\n"
            )
        else:
            category_hint = ""

        from datetime import datetime as _dtm
        _today_str = _dtm.utcnow().strftime("%B %d, %Y")
        prompt = DISCOVER_PROMPT.format(
            site=site_name,
            text=snippets,
            today=_today_str,
            category_hint=category_hint,
        )
        # Try models in order until one works
        _models = [
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.5-flash-preview-05-20",
        ]
        response = None
        for _m in _models:
            try:
                response = _claude.models.generate_content(
                    model=_m,
                    contents=prompt,
                    config=_genai_sdk.types.GenerateContentConfig(
                        response_mime_type="application/json",
                        temperature=0,
                    ),
                )
                print(f"[Mighty] Used model: {_m}", flush=True)
                break
            except Exception as _me:
                print(f"[Mighty] Model {_m} failed: {_me}", flush=True)
        if response is None:
            return []
        text = response.text.strip()
        print(f"[Mighty] Gemini response ({len(text)} chars): {text[:400]}", flush=True)
        fields = []
        try:
            result = json.loads(text)
            if isinstance(result, list):
                fields = _post_filter_fields(result)
            elif isinstance(result, dict):
                for k in ("fields", "data", "items", "results"):
                    if isinstance(result.get(k), list):
                        fields = _post_filter_fields(result[k])
                        break
        except json.JSONDecodeError:
            m = re.search(r'\[.*\]', text, re.DOTALL)
            if m:
                try: fields = _post_filter_fields(json.loads(m.group()))
                except Exception: pass

        # Second pass: ask Gemini to identify missing high-value pages
        # Only run if coverage is low (< 3 high-confidence fields found)
        high_conf_count = sum(1 for f in fields if (f.get("confidence") or 0) >= 0.85)
        if high_conf_count < 3 and source:
            cat_key = None
            for ck, schema in _CATEGORY_SCHEMAS.items():
                if source in schema.get("sources", set()):
                    cat_key = ck
                    break
            expected = _EXPECTED_FIELDS.get(cat_key or "", {})
            if expected:
                found_labels = [f.get("label", "") for f in fields]
                missing_expected = [desc for key, desc in expected.items()
                                   if not any(key.split("_")[0] in fl.lower() for fl in found_labels)]
                if missing_expected:
                    missing_str = ", ".join(missing_expected[:4])
                    page_prompt = (
                        f"Based on this {source} account page text, what specific page URLs or sections "
                        f"are probably missing that would contain: {missing_str}?\n\n"
                        "List only specific paths like /my-account/certificates or /loyalty/wallet. "
                        "Max 5 paths. One per line. No explanation."
                    )
                    try:
                        _gc = _gemini_client()
                        if _gc:
                            page_resp = _gc.models.generate_content(
                                model="gemini-2.0-flash",
                                contents=[{"role": "user", "parts": [{"text": page_prompt + "\n\n" + (raw_text[:2000] if raw_text else "")}]}],
                                config={"temperature": 0.3, "max_output_tokens": 200}
                            )
                            page_text = page_resp.text.strip() if page_resp.text else ""
                            if page_text and source:
                                _store_suggested_paths(source, page_text)
                    except Exception:
                        pass

        _discovery_cache[cache_key] = (_time.time(), fields)
        return fields
    except Exception as e:
        print(f"[Mighty] Gemini discovery error: {e}", flush=True)
        return []


def _store_suggested_paths(site: str, paths_text: str) -> None:
    """Store Gemini-suggested missing paths into site_paths for future crawling."""
    import re as _re
    try:
        db = get_db()
        now = iso()
        for line in paths_text.splitlines():
            line = line.strip().lstrip("- •*123456789.")
            m = _re.search(r'(/[a-z0-9/_\-]+)', line.lower())
            if m:
                path = m.group(1)
                if len(path) > 2:
                    db.execute("""
                        INSERT INTO site_paths (site, path, reporter_count, last_seen, quality_score)
                        VALUES (?, ?, 0, ?, 2.0)
                        ON CONFLICT(site, path) DO UPDATE SET
                            quality_score = MAX(quality_score, 2.0)
                    """, (site, path, now))
        db.commit()
    except Exception:
        pass


def _gemini_client():
    """Return the Gemini client (_claude) if configured, else None."""
    return _claude


def encrypt_cred(user_id: str, value: str) -> str:
    if not value:
        return ""
    f = _cred_fernet(user_id)
    return ("enc:" + f.encrypt(value.encode()).decode()) if f else ("plain:" + value)

def decrypt_cred(user_id: str, stored: str) -> str:
    if not stored:
        return ""
    try:
        if stored.startswith("enc:"):
            return _cred_fernet(user_id).decrypt(stored[4:].encode()).decode()
        if stored.startswith("plain:"):
            return stored[6:]
        return stored
    except Exception:
        return ""

def encrypt_account_data(user_id: str, data: dict) -> str:
    """Encrypt account data JSON. Falls back to plain JSON if cryptography unavailable."""
    f = _data_fernet(user_id)
    payload = json.dumps(data).encode()
    return ("enc:" + f.encrypt(payload).decode()) if f else ("plain:" + payload.decode())

def decrypt_account_data(user_id: str, stored: str) -> dict:
    """Decrypt stored account data. Handles both encrypted and plain fallback."""
    try:
        if stored.startswith("enc:"):
            f = _data_fernet(user_id)
            return json.loads(f.decrypt(stored[4:].encode()))
        if stored.startswith("plain:"):
            return json.loads(stored[6:])
        return json.loads(stored)
    except Exception:
        return {}

def iso():
    return utcnow().isoformat()

def normalize_path(path: str) -> str:
    """Strip query strings, fragments, and personal ID segments from a URL path
    before storing in the shared site_paths registry."""
    path = path.split('?')[0].split('#')[0]
    # Strip UUIDs
    path = re.sub(r'/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '/*', path, flags=re.I)
    # Strip long numeric IDs (≥5 digits)
    path = re.sub(r'/\d{5,}', '/*', path)
    # Strip long alphanumeric tokens (≥20 chars)
    path = re.sub(r'/[a-zA-Z0-9]{20,}', '/*', path)
    return path.rstrip('/') or '/'

def _registry_report_path(source: str, url: str):
    """Report a URL path to the shared site_paths registry. No-op on error."""
    if not source or not url:
        return
    try:
        from urllib.parse import urlparse
        path = normalize_path(urlparse(url).path)
        if not path or path == '/':
            return
        db = get_db()
        db.execute('''
            INSERT INTO site_paths (site, path, reporter_count, last_seen, quality_score)
            VALUES (?, ?, 1, datetime('now'), 1.0)
            ON CONFLICT(site, path) DO UPDATE SET
                reporter_count = reporter_count + 1,
                last_seen      = datetime('now'),
                quality_score  = MIN(10.0, quality_score + 0.5)
        ''', (source, path))
        db.commit()
    except Exception:
        pass

def base_url():
    b = os.environ.get("BASE_URL", "").rstrip("/")
    if b:
        return b
    # Railway (and most reverse proxies) terminate TLS before Flask,
    # so request.url_root comes in as http://. Force https on non-local hosts.
    root = request.url_root.rstrip("/")
    if root.startswith("http://") and "localhost" not in root and "127.0.0.1" not in root:
        root = "https://" + root[len("http://"):]
    return root

def require_login(f):
    @wraps(f)
    def inner(*a, **kw):
        if "user_id" not in session:
            nxt = request.path
            return redirect(f"/login?next={nxt}")
        return f(*a, **kw)
    return inner

def require_login_or_key(f):
    """Accepts either session cookie (web) or X-Mighty-Key header (extension)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Check API key first (extension path — no CSRF needed)
        api_key = request.headers.get("X-Mighty-Key", "").strip()
        if api_key:
            row = get_db().execute(
                "SELECT id FROM users WHERE api_key=?", (api_key,)
            ).fetchone()
            if not row:
                return jsonify({"error": "invalid api key"}), 401
            # Stash user_id in g so the route can access it the same way
            g.api_key_user_id = row["id"]
            return f(*args, **kwargs)
        # Fall back to session cookie (web path)
        if "user_id" not in session:
            nxt = request.path
            return redirect(f"/login?next={nxt}")
        return f(*args, **kwargs)
    return decorated

def get_current_user_id() -> str:
    """Returns user_id whether request came via API key or session cookie."""
    if hasattr(g, "api_key_user_id"):
        return g.api_key_user_id
    return session["user_id"]

def get_csrf_token():
    """Return (and lazily create) a per-session CSRF token."""
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(32)
    return session["_csrf"]

def check_csrf():
    """Abort 403 if CSRF token is missing or wrong (form or header)."""
    from flask import abort
    token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token", "")
    if not token or token != session.get("_csrf", ""):
        abort(403)

def api_user():
    """Return user row from API key in request body or X-Mighty-Key header."""
    data = request.get_json(force=True, silent=True) or {}
    key  = (data.get("api_key") or request.headers.get("X-Mighty-Key", "")).strip()
    # Reject obviously invalid keys before touching the DB (min length = "mk_" + 40 hex chars)
    if not key or len(key) < 10:
        return None, data
    row = get_db().execute("SELECT * FROM users WHERE api_key=?", (key,)).fetchone()
    return row, data

def expire_pending():
    """Mark timed-out pending authorizations as expired."""
    get_db().execute(
        "UPDATE actions SET status='timeout', decided_at=? "
        "WHERE status='pending' AND expires_at < ?",
        (iso(), iso()),
    )
    get_db().commit()

def fmt_time(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str)
        return dt.strftime("%-I:%M %p").lstrip("0") + " · " + dt.strftime("%b %-d")
    except Exception:
        return iso_str[:16]

STATUS_BADGE = {
    "logged":   '<span class="badge badge-logged">Logged</span>',
    "pending":  '<span class="badge badge-pending">Pending</span>',
    "approved": '<span class="badge badge-approved">Approved</span>',
    "denied":   '<span class="badge badge-denied">Denied</span>',
    "timeout":  '<span class="badge badge-timeout">Timed out</span>',
}


# ── Email notifications ───────────────────────────────────────────────────────

def send_authorization_email(to_email, label, action_type, fields, approval_url):
    """Send an authorization request email via Postmark API. Runs in a background thread."""
    if not POSTMARK_API_KEY:
        print("[Mighty] Email skipped — POSTMARK_API_KEY not set", flush=True)
        return

    # Build fields rows
    fields_html = ""
    if fields:
        try:
            for k, v in (fields if isinstance(fields, list) else json.loads(fields)):
                fields_html += f'<tr><td style="padding:6px 0;color:#888;font-size:13px;width:120px;vertical-align:top">{k}</td><td style="padding:6px 0;color:#1a1a1a;font-size:13px">{v}</td></tr>'
        except Exception:
            pass

    fields_section = f'<table style="width:100%;border-collapse:collapse;margin:16px 0">{fields_html}</table>' if fields_html else ""

    html = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f8f7f5;font-family:Arial,sans-serif">
  <div style="max-width:480px;margin:40px auto;padding:0 16px">
    <div style="margin-bottom:20px">
      <span style="font-size:18px;font-weight:700;color:#1a1a1a">⚡ Mighty</span>
    </div>
    <div style="background:#fff;border:1px solid #e5e3df;border-radius:16px;overflow:hidden">
      <div style="background:#f5f3ff;border-bottom:1px solid #e9d5ff;padding:12px 20px">
        <span style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#5b21b6">Authorization Required</span>
      </div>
      <div style="padding:20px">
        <div style="font-size:18px;font-weight:700;color:#1a1a1a;line-height:1.4;margin-bottom:4px">{label}</div>
        <div style="font-size:12px;color:#aaa;font-family:monospace;margin-bottom:8px">{action_type}</div>
        {fields_section}
        <div style="font-size:12px;color:#888;margin-bottom:20px">Your AI agent is waiting. This request expires in 5 minutes.</div>
        <a href="{approval_url}" style="display:block;padding:14px;background:#7c3aed;color:#fff;text-decoration:none;border-radius:10px;font-size:15px;font-weight:700;text-align:center">
          Review &amp; Decide →
        </a>
        <div style="text-align:center;margin-top:10px;font-size:11px;color:#bbb">Opens a page where you can approve or deny.</div>
      </div>
    </div>
  </div>
</body>
</html>"""

    payload = json.dumps({
        "From":     POSTMARK_FROM,
        "To":       to_email,
        "Subject":  f"Action needed: {label}",
        "HtmlBody": html,
    }).encode()

    def _send():
        try:
            req = urllib.request.Request(
                "https://api.postmarkapp.com/email",
                data=payload,
                headers={
                    "X-Postmark-Server-Token": POSTMARK_API_KEY,
                    "Content-Type":            "application/json",
                    "Accept":                  "application/json",
                },
            )
            urllib.request.urlopen(req, timeout=10)
            print("[Mighty] Email sent successfully", flush=True)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            print(f"[Mighty] Email send failed: HTTP {e.code} — {body}", flush=True)
        except Exception as e:
            print(f"[Mighty] Email send failed: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


def ntfy_topic(api_key):
    """Derive a stable, user-specific ntfy.sh topic from their API key."""
    return "mighty-" + api_key[:12]


def send_ntfy_notification(api_key, label, action_type, approval_url):
    """Send a push notification via ntfy.sh. No account or API key required."""
    topic = ntfy_topic(api_key)

    def _send():
        try:
            payload = label.encode()
            req = urllib.request.Request(
                f"https://ntfy.sh/{topic}",
                data=payload,
                headers={
                    "Title":    f"Action needed: {action_type}",
                    "Priority": "high",
                    "Tags":     "rotating_light",
                    "Click":    approval_url,
                    "Actions":  f"view, Review & Decide, {approval_url}",
                },
            )
            urllib.request.urlopen(req, timeout=10)
            print(f"[Mighty] ntfy notification sent to topic {topic}", flush=True)
        except Exception as e:
            print(f"[Mighty] ntfy notification failed: {e}", flush=True)

    threading.Thread(target=_send, daemon=True).start()


def send_password_reset_email(to_email, reset_url):
    """Send a password reset email via Postmark. Falls back to console log if not configured."""
    if not POSTMARK_API_KEY:
        print(f"[Mighty] Password reset link (Postmark not configured): {reset_url}", flush=True)
        return
    html_body = (
        '<!DOCTYPE html><html><body style="margin:0;padding:0;background:#f8f7f5;font-family:Arial,sans-serif">'
        '<div style="max-width:480px;margin:40px auto;padding:0 16px">'
        '<div style="margin-bottom:20px"><span style="font-size:18px;font-weight:700;color:#1a1a1a">&#9889; Mighty</span></div>'
        '<div style="background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:28px">'
        '<div style="font-size:18px;font-weight:700;color:#1a1a1a;margin-bottom:12px">Reset your password</div>'
        '<div style="font-size:14px;color:#555;line-height:1.6;margin-bottom:24px">Click the button below to set a new password. This link expires in 1 hour and can only be used once.</div>'
        f'<a href="{reset_url}" style="display:block;padding:14px;background:#7c3aed;color:#fff;text-decoration:none;border-radius:8px;font-size:15px;font-weight:700;text-align:center">Reset password &rarr;</a>'
        '<div style="margin-top:16px;font-size:12px;color:#9ca3af;text-align:center">If you did not request this, you can safely ignore this email.</div>'
        '</div></div></body></html>'
    )
    payload = json.dumps({
        "From": POSTMARK_FROM,
        "To": to_email,
        "Subject": "Reset your Mighty password",
        "HtmlBody": html_body,
    }).encode()
    def _send():
        try:
            req = urllib.request.Request(
                "https://api.postmarkapp.com/email", data=payload,
                headers={"X-Postmark-Server-Token": POSTMARK_API_KEY,
                         "Content-Type": "application/json", "Accept": "application/json"})
            urllib.request.urlopen(req, timeout=10)
            print("[Mighty] Password reset email sent", flush=True)
        except Exception as e:
            print(f"[Mighty] Reset email failed: {e}", flush=True)
    threading.Thread(target=_send, daemon=True).start()


def send_web_push(user_id, title, body, url, action_id=None):
    """Send a Web Push notification to all subscriptions for a user."""
    if not VAPID_PRIVATE or not VAPID_PUBLIC:
        print("[Mighty] Push skipped — VAPID keys not available", flush=True)
        return
    rows = get_db().execute(
        "SELECT id, subscription FROM push_subscriptions WHERE user_id=?", (user_id,)
    ).fetchall()
    if not rows:
        print(f"[Mighty] No push subscriptions for user {user_id[:8]}", flush=True)
        return

    actions = []
    if action_id:
        actions = [{"action": "open", "title": "Review"}]

    payload = json.dumps({
        "title":   title,
        "body":    body,
        "url":     url,
        "tag":     f"mighty-{action_id[:8] if action_id else 'notif'}",
        "actions": actions,
    })

    sub_ids  = [r["id"] for r in rows]
    sub_data = [(r["id"], json.loads(r["subscription"])) for r in rows]

    def _send():
        from pywebpush import webpush, WebPushException
        import sqlite3 as _sqlite3
        stale_ids = []
        for sub_id, sub in sub_data:
            try:
                webpush(
                    subscription_info=sub,
                    data=payload,
                    vapid_private_key=VAPID_PRIVATE,
                    vapid_claims={"sub": "mailto:noreply@mighty.ai"},
                    content_encoding="aes128gcm",
                )
                print(f"[Mighty] Web push sent to user {user_id[:8]}", flush=True)
            except WebPushException as e:
                resp = e.response
                status = resp.status_code if resp is not None else 0
                print(f"[Mighty] Web push failed ({status}): {e}", flush=True)
                if status in (404, 410, 403):
                    # Subscription expired or invalid — remove it
                    stale_ids.append(sub_id)
            except Exception as e:
                print(f"[Mighty] Web push error: {e}", flush=True)
        if stale_ids:
            with _sqlite3.connect(DATABASE) as db:
                db.executemany("DELETE FROM push_subscriptions WHERE id=?",
                               [(sid,) for sid in stale_ids])
                db.commit()
            print(f"[Mighty] Removed {len(stale_ids)} stale subscription(s)", flush=True)

    threading.Thread(target=_send, daemon=True).start()


# ── HTML ──────────────────────────────────────────────────────────────────────

BASE_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:#eae5de;color:#1c1917;min-height:100vh;-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
a{color:#6366f1;text-decoration:none}
a:hover{text-decoration:underline}
input,textarea,select{font-family:inherit}
button{font-family:inherit;cursor:pointer}
.badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;letter-spacing:0.3px}
.badge-logged{background:#f3f4f6;color:#6b7280}
.badge-pending{background:#fef3c7;color:#92400e;border:1px solid #fde68a}
.badge-approved{background:#d1fae5;color:#065f46;border:1px solid #a7f3d0}
.badge-denied{background:#fee2e2;color:#991b1b;border:1px solid #fca5a5}
.badge-timeout{background:#f3f4f6;color:#6b7280;border:1px solid #e5e7eb}
.sidebar{width:48px;flex-shrink:0;background:#0a0c12;border-right:1px solid rgba(255,255,255,0.06);display:flex;flex-direction:column;height:100vh;overflow:hidden;align-items:center}
.sidebar-header{padding:14px 0 10px;border-bottom:1px solid rgba(255,255,255,0.06);width:100%;display:flex;justify-content:center}
.sidebar-logo{display:flex;align-items:center;justify-content:center;text-decoration:none}
.sidebar-logo:hover{text-decoration:none}
.sidebar-logo-img{width:26px;height:26px;border-radius:7px;object-fit:cover}
.sidebar-nav{flex:1;padding:8px 0;display:flex;flex-direction:column;align-items:center;gap:2px;overflow-y:auto;width:100%}
.sidebar-link{width:36px;height:36px;display:flex;align-items:center;justify-content:center;border-radius:8px;color:#3d4560;text-decoration:none;transition:background 0.1s,color 0.1s}
.sidebar-link:hover{background:rgba(255,255,255,0.07);color:#c4cde0;text-decoration:none}
.sidebar-link svg{flex-shrink:0}
.sidebar-link-active{background:rgba(129,140,248,0.15);color:#818cf8 !important}
.sidebar-footer{padding:10px 0 12px;border-top:1px solid rgba(255,255,255,0.06);width:100%;display:flex;justify-content:center}
.sidebar-avatar{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,#6366f1,#818cf8);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;flex-shrink:0;border:none;cursor:pointer;font-family:inherit;position:relative}
.sidebar-tip{position:fixed;left:54px;background:#1a1d2e;color:#e2e8f0;font-size:12px;font-weight:500;padding:5px 10px;border-radius:7px;white-space:nowrap;pointer-events:none;opacity:0;transition:opacity 0.1s;z-index:999;border:1px solid rgba(255,255,255,0.08)}
.sidebar-link:hover .sidebar-tip,.sidebar-logo:hover .sidebar-tip,.sidebar-avatar:hover .sidebar-tip{opacity:1}
"""

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<meta name="description" content="Mighty adds approval checkpoints and a permanent activity log to any AI agent. Works with Claude, ChatGPT, and custom agents. Set up in 5 minutes. Free to start.">
<title>Mighty — Your AI agents, accountable to you.</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
html{scroll-behavior:smooth}
body{background:#fff;color:#1a1a1a}
/* Nav */
.nav{position:sticky;top:0;z-index:100;background:#fff;border-bottom:1px solid #e5e3df;height:60px;display:flex;align-items:center;padding:0 24px}
.nav-inner{max-width:900px;margin:0 auto;width:100%;display:flex;align-items:center;justify-content:space-between}
.logo{display:flex;align-items:center;gap:10px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center;background:#080a10;border-radius:8px;overflow:hidden}
.logo-mark img{height:32px;width:32px;object-fit:cover}
.logo-name{font-size:18px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.nav-actions{display:flex;align-items:center;gap:16px}
.nav-signin{font-size:14px;font-weight:500;color:#444;text-decoration:none}
.nav-signin:hover{color:#7c3aed;text-decoration:none}
.btn-nav{padding:8px 18px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;text-decoration:none;transition:background 0.12s}
.btn-nav:hover{background:#6d28d9;text-decoration:none;color:#fff}
.nav-hamburger{display:none;background:none;border:none;cursor:pointer;padding:6px;color:#444;line-height:0}
/* Hero — full viewport */
.hero{background:#fff;min-height:calc(100vh - 60px);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:60px 24px 80px;position:relative}
.hero-inner{max-width:600px;margin:0 auto}
.hero h1{font-size:52px;font-weight:800;line-height:1.08;letter-spacing:-1.5px;color:#1a1a1a;margin-bottom:20px}
.hero-sub{font-size:17px;color:#555;line-height:1.65;max-width:460px;margin:0 auto 32px}
.hero-ctas{display:flex;flex-direction:column;align-items:center;gap:12px}
.btn-primary-lg{padding:14px 32px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:700;cursor:pointer;text-decoration:none;transition:background 0.12s;display:inline-block}
.btn-primary-lg:hover{background:#6d28d9;text-decoration:none;color:#fff}
.hero-link{font-size:13px;color:#9ca3af;text-decoration:none;font-weight:500}
.hero-link:hover{color:#6b7280;text-decoration:underline}
.hero-scroll{position:absolute;bottom:28px;left:50%;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center;gap:6px;color:#c4b5fd;font-size:11px;font-weight:500;letter-spacing:0.8px;text-transform:uppercase;cursor:pointer;border:none;outline:none;background:none;padding:0;transition:color 0.12s;font-family:inherit}
.hero-scroll:hover{color:#7c3aed}
.hero-scroll svg{animation:bounce 2s infinite}
@keyframes bounce{0%,100%{transform:translateY(0)}50%{transform:translateY(4px)}}
/* Accordion */
.accordion{max-width:760px;margin:0 auto;padding:0 24px 80px}
.acc-item{border-bottom:1px solid #e5e3df}
.acc-item:first-child{border-top:1px solid #e5e3df}
.acc-header{width:100%;display:flex;align-items:center;justify-content:space-between;padding:22px 0;background:none;border:none;cursor:pointer;text-align:left;gap:16px}
.acc-header:hover .acc-title{color:#7c3aed}
.acc-title{font-size:17px;font-weight:700;color:#1a1a1a;transition:color 0.12s}
.acc-chevron{flex-shrink:0;color:#9ca3af;transition:transform 0.25s ease}
.acc-chevron.open{transform:rotate(180deg)}
.acc-content{max-height:0;overflow:hidden;transition:max-height 0.35s ease, opacity 0.25s ease;opacity:0}
.acc-content.open{opacity:1}
.acc-body{padding:0 0 28px}
/* Steps (inside accordion) */
.section-label{font-size:12px;font-weight:700;letter-spacing:1.5px;color:#7c3aed;text-transform:uppercase;margin-bottom:12px}
.section-title{font-size:28px;font-weight:800;color:#1a1a1a;margin-bottom:36px}
.steps{display:flex;flex-direction:column;gap:28px}
.step{display:flex;align-items:flex-start;gap:20px}
.step-num{width:36px;height:36px;border-radius:50%;background:#7c3aed;color:#fff;font-size:15px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
.step-body h3{font-size:16px;font-weight:700;color:#1a1a1a;margin-bottom:4px}
.step-body p{font-size:14px;color:#555;line-height:1.6}
/* Feature cards (inside accordion) */
.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:20px}
.fcard{background:#fff;border:1.5px solid #e5e3df;border-radius:12px;padding:24px 20px}
.fcard h3{font-size:15px;font-weight:700;color:#1a1a1a;margin-bottom:8px}
.fcard p{font-size:13px;color:#555;line-height:1.6}
.fcard-icon{width:32px;height:32px;border-radius:8px;background:#f3f0ff;display:flex;align-items:center;justify-content:center;margin-bottom:14px}
/* Enterprise form (inside accordion) */
.ent-wrap{max-width:480px}
.enterprise-sub{font-size:15px;color:#555;line-height:1.6;margin-bottom:28px}
.ent-form{background:#f8f7f5;border:1.5px solid #e5e3df;border-radius:12px;padding:28px;text-align:left}
.ent-form label{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px}
.ent-form input,.ent-form textarea{width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;color:#1a1a1a;background:#fff;transition:border-color 0.12s;margin-bottom:14px;font-family:inherit}
.ent-form input:focus,.ent-form textarea:focus{outline:none;border-color:#7c3aed}
.ent-form textarea{height:90px;resize:vertical}
.btn-ent{width:100%;padding:11px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;transition:background 0.12s;cursor:pointer}
.btn-ent:hover{background:#6d28d9}
.ent-thanks{display:none;padding:16px 0;font-size:15px;color:#16a34a;font-weight:600}
/* Footer */
.footer-bar{background:#fff;border-top:1px solid #e5e3df;padding:24px;text-align:center;font-size:13px;color:#9ca3af}
/* ── Mobile (≤ 640px) ────────────────────────────────────────────────────── */
@media(max-width:640px){
  /* Hero */
  .hero{min-height:calc(100vh - 60px);padding:48px 20px 72px}
  .hero h1{font-size:36px;letter-spacing:-0.5px;margin-bottom:16px}
  .hero-sub{font-size:16px;max-width:100%}
  .hero-ctas{gap:10px}
  .btn-primary-lg{width:100%;text-align:center;padding:14px 20px;font-size:16px}
  .hero-link{font-size:14px}
  .hero-scroll{bottom:20px;font-size:10px}
  /* Accordion */
  .accordion{padding:0 16px 60px}
  .acc-header{padding:18px 0}
  .acc-title{font-size:16px}
  /* Steps */
  .steps{gap:24px}
  .step{gap:14px}
  .step-num{width:30px;height:30px;font-size:13px;flex-shrink:0}
  .step-body h3{font-size:15px}
  .step-body p{font-size:14px}
  /* Feature cards */
  .cards{grid-template-columns:1fr}
  /* Enterprise form */
  .ent-form{padding:20px 16px}
  /* Footer */
  .footer-bar{padding:20px 16px;font-size:12px}
}
</style>
</head>
<body>

<!-- Nav -->
<nav class="nav">
  <div class="nav-inner">
    <a href="/" class="logo" style="text-decoration:none">
      <div class="logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
      <div class="logo-name">Mighty</div>
    </a>
    <div class="nav-actions">
      <a href="/login" class="nav-signin">Sign in</a>
      <a href="/signup" class="btn-nav">Create account</a>
    </div>
  </div>
</nav>

<!-- Hero -->
<section class="hero">
  <div class="hero-inner">
    <h1>Your AI, accountable.</h1>
    <p class="hero-sub">Keep tabs on your agent's most consequential actions — what it did, what you approved, and when.</p>
    <div class="hero-ctas">
      <a href="/signup" class="btn-primary-lg">Create account &rarr;</a>
      <a href="/login" class="hero-link">Sign in &rarr;</a>
      <a href="#more" class="hero-link">Using Mighty for a team? &rarr;</a>
    </div>
  </div>
  <button class="hero-scroll" onclick="openFirst()" aria-label="Learn more">
    <span>Learn more</span>
    <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="4,6 8,10 12,6"/></svg>
  </button>
</section>


<!-- Accordion -->
<div class="accordion" id="more">

  <!-- How it works -->
  <div class="acc-item">
    <button class="acc-header" onclick="toggleAcc('hiw')" id="hiw-btn">
      <span class="acc-title">How it works</span>
      <svg class="acc-chevron" id="hiw-chevron" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="4,7 9,12 14,7"/></svg>
    </button>
    <div class="acc-content" id="hiw">
      <div class="acc-body">
        <div class="steps">
          <div class="step">
            <div class="step-num">1</div>
            <div class="step-body">
              <h3>Connect your agent</h3>
              <p>Add the Mighty system prompt to ChatGPT, or install the MCP plugin for Claude Desktop. Works with any custom agent via API. Takes about 5 minutes.</p>
            </div>
          </div>
          <div class="step">
            <div class="step-num">2</div>
            <div class="step-body">
              <h3>Your agent asks before acting</h3>
              <p>Before anything consequential — sending an email, making a purchase, editing a file — your agent checks with you first.</p>
            </div>
          </div>
          <div class="step">
            <div class="step-num">3</div>
            <div class="step-body">
              <h3>You decide. Mighty keeps the record.</h3>
              <p>Your decision is logged independently — separate from your AI, which can't edit or delete it. Detailed enough to prove what happened, when you need to.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>


  <!-- For teams -->
  <div class="acc-item" id="enterprise">
    <button class="acc-header" onclick="toggleAcc('teams')" id="teams-btn">
      <span class="acc-title">Using Mighty for a team?</span>
      <svg class="acc-chevron" id="teams-chevron" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="4,7 9,12 14,7"/></svg>
    </button>
    <div class="acc-content" id="teams">
      <div class="acc-body">
        <div class="ent-wrap">
          <p class="enterprise-sub">When teams give AI agents real authority — over email, purchasing, contracts, or operations — an independent record isn't optional, it's essential. Tell us about your use case.</p>
          <div id="ent-form-wrap">
            <form id="ent-form" class="ent-form">
              <label>Full name</label>
              <input type="text" id="ent-name" placeholder="Jane Smith" required>
              <label>Work email</label>
              <input type="email" id="ent-email" placeholder="jane@company.com" required>
              <label>Company</label>
              <input type="text" id="ent-company" placeholder="Acme Corp">
              <label>Tell us about your use case <span style="font-weight:400;color:#aaa">(optional)</span></label>
              <textarea id="ent-message" placeholder="We are deploying agents that..."></textarea>
              <button type="submit" class="btn-ent" id="enterprise-submit-btn">Get in touch &rarr;</button>
            </form>
            <div class="ent-thanks" id="ent-thanks">Thanks — we will be in touch within one business day.</div>
          </div>
        </div>
      </div>
    </div>
  </div>

</div>

<!-- Footer -->
<div class="footer-bar">&copy; <span id="cy">2026</span> Mighty &middot; <a href="/privacy" style="color:#9ca3af;text-decoration:none">Privacy</a> &middot; <a href="/tos" style="color:#9ca3af;text-decoration:none">Terms</a></div>

<script>
document.getElementById('cy').textContent = new Date().getFullYear();

function toggleNav() {
  var nav = document.getElementById('nav-actions');
  nav.classList.toggle('open');
}
document.addEventListener('click', function(e) {
  var btn = document.getElementById('nav-menu-btn');
  var nav = document.getElementById('nav-actions');
  if (btn && nav && !btn.contains(e.target) && !nav.contains(e.target)) {
    nav.classList.remove('open');
  }
});

// Accordion
function toggleAcc(id) {
  var content  = document.getElementById(id);
  var chevron  = document.getElementById(id + '-chevron');
  var isOpen   = content.classList.contains('open');
  // close all
  document.querySelectorAll('.acc-content').forEach(function(el) {
    el.classList.remove('open');
    el.style.maxHeight = '0';
  });
  document.querySelectorAll('.acc-chevron').forEach(function(el) {
    el.classList.remove('open');
  });
  // open clicked if it was closed
  if (!isOpen) {
    content.classList.add('open');
    content.style.maxHeight = content.scrollHeight + 'px';
    chevron.classList.add('open');
  }
}

function openFirst() {
  var btn = document.querySelector('.hero-scroll');
  if (btn) btn.style.display = 'none';
  var first = document.querySelector('.acc-item');
  if (first) first.scrollIntoView({behavior: 'smooth', block: 'start'});
}

document.getElementById("ent-form").addEventListener("submit", function(e) {
  e.preventDefault();
  var name    = document.getElementById("ent-name").value.trim();
  var email   = document.getElementById("ent-email").value.trim();
  var company = document.getElementById("ent-company").value.trim();
  var message = document.getElementById("ent-message").value.trim();
  var btn     = document.getElementById("enterprise-submit-btn");
  if (btn) { btn.disabled = true; btn.textContent = "Sending..."; }
  fetch("/enterprise-interest", {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify({name: name, email: email, company: company, message: message})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      document.getElementById("ent-form").style.display = "none";
      document.getElementById("ent-thanks").style.display = "block";
      // re-measure accordion height
      var tc = document.getElementById('teams');
      if (tc && tc.classList.contains('open')) tc.style.maxHeight = tc.scrollHeight + 'px';
    } else {
      btn.textContent = "Error — please try again";
      btn.disabled = false;
    }
  }).catch(function() {
    btn.textContent = "Error — please try again";
    btn.disabled = false;
  });
});
</script>

</body>
</html>"""

SIGNUP_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Create account — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;overflow-y:auto;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center;background:#080a10;border-radius:8px;overflow:hidden}
.logo-mark img{height:32px;width:32px;object-fit:cover}
.logo-name{font-size:18px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
h1{font-size:22px;font-weight:700;margin-bottom:6px;color:#1a1a1a}
.sub{font-size:14px;color:#666;margin-bottom:24px;line-height:1.5}
label{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px}
input[type=email],input[type=password]{width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;color:#1a1a1a;background:#fff;transition:border-color 0.12s;margin-bottom:14px}
input:focus{outline:none;border-color:#7c3aed}
.btn-primary{width:100%;padding:12px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;transition:background 0.12s;margin-top:4px}
.btn-primary:hover{background:#6d28d9}
.err{font-size:13px;color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:7px;padding:9px 12px;margin-bottom:14px}
.footer{text-align:center;margin-top:20px;font-size:13px;color:#888}
.back{display:block;font-size:13px;color:#888;margin-bottom:20px;text-decoration:none}
.back:hover{color:#7c3aed;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <a href="/" class="back">&larr; Home</a>
  <div class="logo">
    <div class="logo-mark">
      <img src="/logo-icon.png" alt="Mighty">
    </div>
    <div class="logo-name">Mighty</div>
  </div>
  <h1>Create your account</h1>
  <p class="sub">You'll be connected in about 5 minutes.</p>
  {error}
  <form method="POST" action="/signup">
<input type="hidden" name="_csrf" value="{csrf_token}">
    <label>Email</label>
    <input type="email" name="email" placeholder="you@example.com" required autocomplete="email">
    <label>Password</label>
    <input type="password" name="password" placeholder="Choose a password" required autocomplete="new-password" minlength="6" maxlength="128">
    <button class="btn-primary" type="submit">Create account &rarr;</button>
  </form>
  <div class="footer">Already have an account? <a href="/login">Sign in</a></div>
  <div style="text-align:center;margin-top:8px;font-size:12px;color:#9ca3af">By signing up you agree to our <a href="/tos" style="color:#9ca3af">Terms</a> and <a href="/privacy" style="color:#9ca3af">Privacy Policy</a>.</div>
</div>
</body>
</html>"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Sign in — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;overflow-y:auto;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center;background:#080a10;border-radius:8px;overflow:hidden}
.logo-mark img{height:32px;width:32px;object-fit:cover}
.logo-name{font-size:18px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
h1{font-size:22px;font-weight:700;margin-bottom:6px}
.sub{font-size:14px;color:#666;margin-bottom:20px}
label{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px}
input[type=email],input[type=password]{width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;color:#1a1a1a;background:#fff;transition:border-color 0.12s;margin-bottom:14px}
input:focus{outline:none;border-color:#7c3aed}
.btn-primary{width:100%;padding:12px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;transition:background 0.12s}
.btn-primary:hover{background:#6d28d9}
.err{font-size:13px;color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:7px;padding:9px 12px;margin-bottom:14px}
.footer{text-align:center;margin-top:20px;font-size:13px;color:#888}
.back{display:block;font-size:13px;color:#888;margin-bottom:20px;text-decoration:none}.back:hover{color:#7c3aed;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <a href="/" class="back">&larr; Home</a>
  <div class="logo">
    <div class="logo-mark">
      <img src="/logo-icon.png" alt="Mighty">
    </div>
    <div class="logo-name">Mighty</div>
  </div>
  <h1>Welcome back</h1>
  <p class="sub">Sign in to your Mighty account.</p>
  {error}
  <form method="POST" action="/login">
<input type="hidden" name="_csrf" value="{csrf_token}">
    <input type="hidden" name="next" id="next-field" value="">
    <label>Email</label>
    <input type="email" name="email" placeholder="you@example.com" required autocomplete="email">
    <label>Password</label>
    <input type="password" name="password" placeholder="Your password" required autocomplete="current-password" maxlength="128">
    <button class="btn-primary" type="submit">Sign in →</button>
  </form>
  <div style="text-align:center;margin-top:12px;font-size:12px;color:#9ca3af">
    Forgot your password? <a href="/forgot-password" style="color:#7c3aed">Reset it here</a>
  </div>
  <div class="footer">No account? <a href="/signup">Sign up free</a></div>
</div>
<script>
var nf = document.getElementById('next-field');
if (nf) nf.value = new URLSearchParams(window.location.search).get('next') || '';
</script>
</body>
</html>"""

FORGOT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Reset password — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;overflow-y:auto;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center;background:#080a10;border-radius:8px;overflow:hidden}
.logo-mark img{height:32px;width:32px;object-fit:cover}
.logo-name{font-size:18px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
h1{font-size:22px;font-weight:700;margin-bottom:8px;color:#1a1a1a}
.sub{font-size:14px;color:#666;margin-bottom:24px;line-height:1.5}
label{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px}
input[type=email]{width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;color:#1a1a1a;background:#fff;transition:border-color 0.12s;margin-bottom:14px}
input:focus{outline:none;border-color:#7c3aed}
.btn-primary{width:100%;padding:12px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;transition:background 0.12s;margin-top:4px}
.btn-primary:hover{background:#6d28d9}
.info{font-size:13px;color:#16a34a;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:7px;padding:9px 12px;margin-bottom:14px}
.err{font-size:13px;color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:7px;padding:9px 12px;margin-bottom:14px}
.footer{text-align:center;margin-top:20px;font-size:13px;color:#888}
.back{display:block;font-size:13px;color:#888;margin-bottom:20px;text-decoration:none}
.back:hover{color:#7c3aed;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <a href="/login" class="back">&larr; Back to sign in</a>
  <div class="logo">
    <div class="logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
    <div class="logo-name">Mighty</div>
  </div>
  <h1>Forgot your password?</h1>
  <p class="sub">Enter your email and we&rsquo;ll send you a reset link.</p>
  {message}
  <form method="POST" action="/forgot-password">
<input type="hidden" name="_csrf" value="{csrf_token}">
    <label>Email</label>
    <input type="email" name="email" placeholder="you@example.com" required autocomplete="email">
    <button class="btn-primary" type="submit">Send reset link &rarr;</button>
  </form>
</div>
</body>
</html>"""

RESET_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Set new password — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;overflow-y:auto;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center;background:#080a10;border-radius:8px;overflow:hidden}
.logo-mark img{height:32px;width:32px;object-fit:cover}
.logo-name{font-size:18px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
h1{font-size:22px;font-weight:700;margin-bottom:8px;color:#1a1a1a}
.sub{font-size:14px;color:#666;margin-bottom:24px}
label{display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px}
input[type=password]{width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;color:#1a1a1a;background:#fff;transition:border-color 0.12s;margin-bottom:6px}
input:focus{outline:none;border-color:#7c3aed}
.hint{font-size:12px;color:#9ca3af;margin-bottom:14px}
.btn-primary{width:100%;padding:12px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;transition:background 0.12s;margin-top:4px}
.btn-primary:hover{background:#6d28d9}
.err{font-size:13px;color:#dc2626;background:#fef2f2;border:1px solid #fecaca;border-radius:7px;padding:9px 12px;margin-bottom:14px}
.back{display:block;font-size:13px;color:#888;margin-bottom:20px;text-decoration:none}.back:hover{color:#7c3aed;text-decoration:none}
</style>
</head>
<body>
<div class="card">
  <a href="/login" class="back">&larr; Back to sign in</a>
  <div class="logo">
    <div class="logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
    <div class="logo-name">Mighty</div>
  </div>
  <h1>Set a new password</h1>
  <p class="sub">Choose a strong password for your Mighty account.</p>
  {error}
  <form method="POST">
<input type="hidden" name="_csrf" value="{csrf_token}">
    <label>New password</label>
    <input type="password" name="password" placeholder="At least 6 characters" required minlength="6" maxlength="128" autocomplete="new-password">
    <div class="hint">At least 6 characters.</div>
    <label>Confirm password</label>
    <input type="password" name="confirm" placeholder="Repeat your password" required maxlength="128" autocomplete="new-password">
    <button class="btn-primary" type="submit" style="margin-top:14px">Set new password &rarr;</button>
  </form>
</div>
</body>
</html>"""

PRIVACY_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Privacy — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{background:#f8f7f5}
.nav{background:#fff;border-bottom:1px solid #e5e3df;height:52px;display:flex;align-items:center;padding:0 24px}
.nav a{font-size:14px;color:#7c3aed;text-decoration:none;font-weight:500}
.nav a:hover{text-decoration:underline}
.wrap{max-width:680px;margin:0 auto;padding:48px 24px}
h1{font-size:28px;font-weight:800;color:#1a1a1a;margin-bottom:8px}
.updated{font-size:13px;color:#9ca3af;margin-bottom:40px}
h2{font-size:17px;font-weight:700;color:#1a1a1a;margin:32px 0 10px}
p,li{font-size:15px;color:#374151;line-height:1.7}
p{margin-bottom:14px}
ul{margin:0 0 14px 20px}
li{margin-bottom:6px}
a{color:#7c3aed}
</style>
</head>
<body>
<nav class="nav"><a href="/">&larr; Home</a></nav>
<div class="wrap">
  <h1>Privacy Policy</h1>
  <div class="updated">Last updated: May 2026</div>

  <h2>What Mighty stores</h2>
  <p>When you use Mighty, we store:</p>
  <ul>
    <li>Your email address and hashed password (bcrypt)</li>
    <li>A randomly generated API key used to authenticate your agent</li>
    <li>Action logs submitted by your agent &mdash; the action type, label, and any detail fields your agent provides</li>
    <li>Your notification preferences (email, push, ntfy)</li>
    <li>Browser push subscription tokens, if you enable push notifications</li>
  </ul>
  <p>All data is stored in a private database. We do not store plaintext passwords.</p>

  <h2>What Mighty does not store</h2>
  <ul>
    <li>The full content of your AI agent conversations</li>
    <li>Any data beyond what your agent explicitly sends via the Mighty API</li>
    <li>Payment information (Mighty is free to use)</li>
  </ul>

  <h2>Who can see your data</h2>
  <p>Your action logs and account data are private to your account. We do not sell or share your data with third parties. Mighty team members may access data for debugging or support purposes only.</p>

  <h2>Third-party services</h2>
  <ul>
    <li><strong>Postmark</strong> &mdash; used to send authorization request emails and password reset emails. Only the action label and a private approval link are included in emails.</li>
    <li><strong>ntfy.sh</strong> &mdash; an open-source notification service. If you enable mobile alerts, only the action label and approval URL are sent to ntfy.sh. No account data is transmitted.</li>
  </ul>

  <h2>Data retention and deletion</h2>
  <p>Your data is retained until you delete it. You can export your full activity log as a CSV or delete all your data at any time from your <a href="/settings">Settings</a> page. Account deletion is immediate and permanent.</p>

  <h2>Contact</h2>
  <p>Questions about your data? Email <a href="mailto:support@mighty.app">support@mighty.app</a>.</p>
</div>
</body>
</html>"""

TOS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Terms of Service — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{background:#f8f7f5}
.nav{background:#fff;border-bottom:1px solid #e5e3df;height:52px;display:flex;align-items:center;padding:0 24px}
.nav a{font-size:14px;color:#7c3aed;text-decoration:none;font-weight:500}
.nav a:hover{text-decoration:underline}
.wrap{max-width:680px;margin:0 auto;padding:48px 24px}
h1{font-size:28px;font-weight:800;color:#1a1a1a;margin-bottom:8px}
.updated{font-size:13px;color:#9ca3af;margin-bottom:40px}
h2{font-size:17px;font-weight:700;color:#1a1a1a;margin:32px 0 10px}
p,li{font-size:15px;color:#374151;line-height:1.7}
p{margin-bottom:14px}
ul{margin:0 0 14px 20px}
li{margin-bottom:6px}
a{color:#7c3aed}
</style>
</head>
<body>
<nav class="nav"><a href="/">&larr; Home</a></nav>
<div class="wrap">
  <h1>Terms of Service</h1>
  <div class="updated">Last updated: May 2026</div>

  <h2>Acceptance</h2>
  <p>By creating a Mighty account you agree to these terms. If you don't agree, don't use the service.</p>

  <h2>What Mighty is</h2>
  <p>Mighty is an independent record of what your AI agents do on your behalf. It provides an API and dashboard for logging consequential actions — what the agent reported it was going to do, and whether you approved it. Mighty does not operate your AI agents — you are responsible for how you configure and use them.</p>

  <h2>Your account</h2>
  <p>You are responsible for keeping your API key and password secure. Do not share your API key with untrusted parties. You are responsible for all activity that occurs under your account.</p>

  <h2>Acceptable use</h2>
  <p>You may not use Mighty to facilitate illegal activity, to harm others, or to abuse the service infrastructure. We reserve the right to terminate accounts that violate these terms.</p>

  <h2>Data</h2>
  <p>We store the data necessary to operate the service — see our <a href="/privacy">Privacy Policy</a> for details. You can export or delete your data at any time from your Settings page.</p>

  <h2>Availability</h2>
  <p>Mighty is provided as-is. We make no guarantees about uptime, reliability, or fitness for a particular purpose. The service may change or be discontinued at any time.</p>

  <h2>Limitation of liability</h2>
  <p>Mighty is not liable for any actions taken by your AI agents, whether authorized through the service or not. You are solely responsible for the actions of agents you connect to Mighty.</p>

  <h2>Changes</h2>
  <p>We may update these terms from time to time. Continued use of the service after changes constitutes acceptance of the new terms.</p>

  <h2>Contact</h2>
  <p>Questions? Email <a href="mailto:support@mighty.app">support@mighty.app</a>.</p>
</div>
</body>
</html>"""

NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Page not found — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;background:#f8f7f5}
.wrap{text-align:center;max-width:380px}
.logo{display:flex;align-items:center;gap:10px;justify-content:center;margin-bottom:32px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center;background:#080a10;border-radius:8px;overflow:hidden}
.logo-mark img{height:32px;width:32px;object-fit:cover}
.logo-name{font-size:18px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
h1{font-size:72px;font-weight:800;color:#e5e3df;margin-bottom:0;line-height:1}
h2{font-size:20px;font-weight:700;color:#1a1a1a;margin:8px 0 12px}
p{font-size:14px;color:#6b7280;line-height:1.6;margin-bottom:28px}
.btn{display:inline-block;padding:11px 24px;background:#7c3aed;color:#fff;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;transition:background 0.12s}
.btn:hover{background:#6d28d9;text-decoration:none;color:#fff}
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">
    <div class="logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
    <div class="logo-name">Mighty</div>
  </div>
  <h1>404</h1>
  <h2>Page not found</h2>
  <p>The page you&rsquo;re looking for doesn&rsquo;t exist or has been moved.</p>
  <a href="/" class="btn">Go home</a>
</div>
</body>
</html>"""

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Dashboard — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
html,body{height:100%;overflow:hidden}
body{display:flex;flex-direction:row;background:#eee9e2}
.main-content{flex:1;min-width:0;height:100vh;overflow:hidden;display:flex;flex-direction:column}
/* Top bar */
.topbar{padding:14px 24px;display:flex;align-items:center;gap:10px;flex-shrink:0;background:#eee9e2;z-index:2;border-bottom:0.5px solid rgba(0,0,0,0.07)}
.topbar-search{flex:1;display:flex;align-items:center;gap:8px;background:#fff;border:0.5px solid rgba(0,0,0,0.1);border-radius:9px;padding:8px 14px;cursor:text;max-width:340px}
.topbar-search input{border:none;outline:none;font-size:13px;color:#1c1917;background:transparent;width:100%;font-family:inherit}
.topbar-search input::placeholder{color:#c0bab4}
.topbar-search svg{flex-shrink:0;color:#b0aaa4}
.expiring-pill{font-size:11px;font-weight:600;color:#d97706;background:rgba(217,119,6,0.1);border:0.5px solid rgba(217,119,6,0.25);border-radius:20px;padding:4px 11px;display:flex;align-items:center;gap:5px;white-space:nowrap}
.pending-pill{background:rgba(99,102,241,0.1);border:0.5px solid rgba(99,102,241,0.22);border-radius:20px;padding:4px 11px;font-size:11px;font-weight:600;color:#6366f1;white-space:nowrap}
.btn-sync{padding:7px 14px;border-radius:8px;border:0.5px solid rgba(0,0,0,0.12);background:#ffffff;font-size:12px;font-weight:600;color:#1c1917;cursor:pointer;transition:all 0.12s;font-family:inherit;box-shadow:0 1px 2px rgba(0,0,0,0.05);white-space:nowrap;display:inline-flex;align-items:center;gap:6px}
.btn-sync:hover{border-color:#6366f1;color:#6366f1}
@keyframes spin-sync{to{transform:rotate(360deg)}}
.btn-sync.syncing #sync-icon{animation:spin-sync 0.8s linear infinite}
.btn-sync.rediscovering{border-color:#6366f1;color:#6366f1;background:#f5f3ff}
.btn-sync.rediscovering #rediscover-icon{animation:spin-sync 0.9s linear infinite}
#mighty-toast{position:fixed;bottom:24px;left:50%;transform:translateX(-50%) translateY(0);background:#1c1917;color:#fff;font-size:13px;font-weight:500;padding:10px 18px;border-radius:10px;box-shadow:0 4px 16px rgba(0,0,0,0.18);z-index:9999;pointer-events:none;opacity:0;transition:opacity 0.2s,transform 0.2s}
#mighty-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
#mighty-toast.hide{opacity:0;transform:translateX(-50%) translateY(8px)}
/* Feed tabs */
.feed-tabs{display:flex;gap:0;background:#e4dfd8;border:0.5px solid #d5cfc8;border-radius:9px;padding:3px;width:fit-content}
.feed-tab{padding:5px 18px;border-radius:6px;border:none;background:none;font-size:12px;font-weight:600;color:#7d7670;cursor:pointer;transition:all 0.12s;font-family:inherit}
.feed-tab.active{background:#ffffff;color:#1c1917;box-shadow:0 1px 3px rgba(0,0,0,0.10)}
/* Page body — single column: intelligence strip at top, accounts below */
.page-body{flex:1;display:flex;flex-direction:column;min-height:0;overflow-y:auto;padding:0}
.insight-panel{width:100%;padding:24px 32px 22px;background:#f7f4f0;border-bottom:1px solid #e5e0da;flex-shrink:0}
.insight-inner{max-width:820px;margin:0 auto}
/* Cards panel — full-width with generous max so wide monitors breathe */
.cards-panel{flex:1;min-width:0;padding:20px 28px 40px}
.cards-panel-inner{max-width:1600px;margin:0 auto}
.cards-panel-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:16px}
/* Compact (utility/low-value) account cards — subdued when not expanded */
.acct-card.is-compact{opacity:0.72;border-color:rgba(0,0,0,0.06)}
.acct-card.is-compact:not(.is-expanded) .acct-secondary{display:none}
.acct-card.is-compact:hover{opacity:0.92}
.acct-card.is-compact.is-expanded{opacity:1}
/* Category groups */
.cat-group{margin-bottom:22px}
.cat-header{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.cat-label{font-size:11px;font-weight:600;color:#a09a94;text-transform:uppercase;letter-spacing:0.7px;white-space:nowrap}
.cat-rule{flex:1;height:0.5px;background:rgba(0,0,0,0.08)}
/* Card grid */
.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:10px}
/* Account cards */
.acct-card{background:#ffffff;border-radius:12px;overflow:hidden;border:0.5px solid rgba(0,0,0,0.08);box-shadow:0 1px 1px rgba(0,0,0,0.03),0 3px 12px rgba(0,0,0,0.05);transition:box-shadow 0.2s,border-color 0.2s,opacity 0.2s,transform 0.2s,filter 0.2s}
.acct-card.is-syncing{border-color:rgba(99,102,241,0.3);box-shadow:0 0 0 2px rgba(99,102,241,0.08),0 3px 12px rgba(0,0,0,0.05);animation:card-pulse 1.8s ease-in-out infinite}
@keyframes card-pulse{0%,100%{box-shadow:0 0 0 2px rgba(99,102,241,0.08),0 3px 12px rgba(0,0,0,0.05)}50%{box-shadow:0 0 0 3px rgba(99,102,241,0.18),0 3px 12px rgba(0,0,0,0.05)}}
.acct-card:hover{border-color:rgba(0,0,0,0.14);box-shadow:0 2px 4px rgba(0,0,0,0.05),0 8px 28px rgba(0,0,0,0.08)}
.acct-card-header{padding:12px 14px 10px;display:flex;align-items:center;gap:9px}
.acct-name{font-size:13px;font-weight:700;color:#1c1917;line-height:1.2}
.acct-sync-time{font-size:10px;color:#b8b2ac;margin-top:1px}
.acct-controls{display:flex;align-items:center;gap:5px;margin-left:auto;flex-shrink:0}
.acct-refresh-btn{width:24px;height:24px;display:flex;align-items:center;justify-content:center;border-radius:6px;border:0.5px solid #ede9e4;background:transparent;cursor:pointer;font-size:13px;color:#b8b2ac;padding:0;line-height:1;transition:all 0.12s;font-family:inherit}
.acct-refresh-btn:hover{color:#6366f1;border-color:#c7d2fe;background:#f5f3ff}
/* Hero stat */
.acct-divider{height:0.5px;background:rgba(0,0,0,0.06);margin:0 14px}
.acct-hero{padding:10px 14px 6px}
.hero-val{font-size:20px;font-weight:700;color:#1c1917;letter-spacing:-0.4px;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hero-lbl{font-size:11px;font-weight:600;color:#b8b2ac;text-transform:uppercase;letter-spacing:0.6px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
/* Secondary stats */
.acct-secondary{padding:4px 14px 12px;display:flex;flex-direction:column;gap:3px}
.sec-row{display:flex;justify-content:space-between;align-items:baseline;gap:8px}
.sec-lbl{font-size:12px;color:#b8b2ac;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:1}
.sec-val{font-size:12px;font-weight:600;color:#374151;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex-shrink:0;max-width:55%}
/* Time-sensitive alert row */
.acct-alert{margin:0 10px 10px;border-radius:7px;padding:7px 10px;display:flex;align-items:flex-start;gap:7px}
.acct-alert-amber{background:#fffbeb;border:0.5px solid rgba(217,119,6,0.3)}
.acct-alert-red{background:#fef2f2;border:0.5px solid rgba(220,38,38,0.2)}
.alert-icon{font-size:12px;flex-shrink:0;margin-top:1px}
.alert-lbl{font-size:12px;font-weight:600;line-height:1.3}
.alert-amber .alert-lbl,.acct-alert-amber .alert-lbl{color:#92400e}
.alert-sub{font-size:11px;margin-top:1px}
.acct-alert-amber .alert-sub{color:#b45309}
.acct-alert-red .alert-lbl{color:#991b1b}
.acct-alert-red .alert-sub{color:#b91c1c}
/* States */
.acct-card.is-stale{opacity:0.65}
.acct-card.is-expiring{border-color:rgba(217,119,6,0.35) !important;box-shadow:0 1px 1px rgba(0,0,0,0.03),0 3px 12px rgba(217,119,6,0.1) !important}
.acct-card.highlight-off{opacity:0.28;filter:grayscale(0.3)}
.acct-card.highlight-on{box-shadow:0 4px 20px rgba(0,0,0,0.12),0 0 0 2.5px rgba(37,99,235,0.7) !important;transform:translateY(-2px);background:#fff !important}
/* Card footer */
.acct-footer{display:flex;align-items:center;justify-content:space-between;padding:8px 14px;border-top:0.5px solid rgba(0,0,0,0.05);gap:8px}
.acct-expand-btn{font-size:11px;font-weight:500;color:#6b7280;background:none;border:none;cursor:pointer;padding:0;font-family:inherit;display:flex;align-items:center;gap:4px;transition:color 0.1s}
.acct-expand-btn:hover{color:#6366f1}
.acct-expand-btn svg{transition:transform 0.15s}
.acct-card.is-expanded .acct-expand-btn svg{transform:rotate(180deg)}
.acct-edit-btn{font-size:11px;font-weight:500;color:#9ca3af;text-decoration:none;padding:3px 8px;border-radius:5px;border:0.5px solid #ede9e4;background:#faf8f6;white-space:nowrap;transition:all 0.12s}
.acct-edit-btn:hover{color:#6366f1;border-color:#c7d2fe;background:#f5f3ff;text-decoration:none}
/* Expanded fields */
.acct-expanded{display:none;padding:6px 14px 10px}
.acct-card.is-expanded .acct-expanded{display:block}
.exp-row{display:flex;justify-content:space-between;align-items:baseline;gap:8px;padding:3px 0;border-bottom:0.5px solid rgba(0,0,0,0.04)}
.exp-row:last-child{border-bottom:none}
.exp-lbl{font-size:11px;color:#b8b2ac;flex-shrink:1;min-width:32%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.exp-val{font-size:12px;font-weight:600;color:#374151;text-align:right;flex-shrink:0;max-width:62%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* Activity log */
.action-card{background:#ffffff;border:0.5px solid rgba(0,0,0,0.08);border-radius:12px;overflow:hidden;margin-bottom:8px;transition:all 0.12s;box-shadow:0 1px 1px rgba(0,0,0,0.03),0 3px 12px rgba(0,0,0,0.05)}
.action-card:hover{border-color:rgba(0,0,0,0.13);box-shadow:0 2px 4px rgba(0,0,0,0.05),0 6px 20px rgba(0,0,0,0.08)}
.action-card.is-pending{border-color:rgba(245,158,11,0.3);background:linear-gradient(180deg,#fffcf0 0%,#fff 100%)}
.action-top{padding:14px 16px 0;display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.action-label{font-size:14px;font-weight:600;color:#1c1917;line-height:1.4}
.action-type{font-size:11px;color:#b0aaa4;font-family:ui-monospace,monospace;margin-top:2px}
.action-badges{display:flex;align-items:center;gap:6px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end}
.action-time{font-size:11px;color:#b0aaa4;margin-top:4px;text-align:right}
.action-fields{padding:10px 16px 14px;display:flex;flex-direction:column;gap:5px}
.field-row{display:flex;gap:10px;font-size:12px}
.field-key{color:#b0aaa4;font-weight:600;text-transform:uppercase;font-size:10px;letter-spacing:0.6px;min-width:80px;flex-shrink:0;padding-top:1px}
.field-val{color:#6b7280;line-height:1.4;word-break:break-word}
.action-buttons{padding:12px 16px;border-top:1px solid #f5f2ed;display:flex;gap:8px}
.btn-authorize{flex:1;padding:9px;background:rgba(52,211,153,0.08);color:#059669;border:0.5px solid rgba(52,211,153,0.2);border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all 0.12s;font-family:inherit}
.btn-authorize:hover{background:rgba(52,211,153,0.15);border-color:#34d399}
.btn-reject{flex:1;padding:9px;background:rgba(248,113,113,0.05);color:#dc2626;border:0.5px solid rgba(248,113,113,0.15);border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all 0.12s;font-family:inherit}
.btn-reject:hover{background:rgba(248,113,113,0.1);border-color:#f87171}
.clevel-sensitive{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:rgba(59,130,246,0.1);color:#3b82f6;border:0.5px solid rgba(59,130,246,0.18)}
.clevel-consequential{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:rgba(251,191,36,0.1);color:#d97706;border:0.5px solid rgba(251,191,36,0.18)}
.clevel-critical{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:rgba(248,113,113,0.1);color:#dc2626;border:0.5px solid rgba(248,113,113,0.18)}
.feed-search{width:100%;padding:9px 14px;border:0.5px solid #ddd8d2;border-radius:9px;font-size:13px;font-family:inherit;outline:none;color:#1c1917;background:#ffffff;transition:border-color 0.12s,box-shadow 0.12s;margin-bottom:14px}
.feed-search:focus{border-color:#6366f1;box-shadow:0 0 0 3px rgba(99,102,241,0.08)}
.feed-search::placeholder{color:#c0bbb5}
.status-chip{padding:5px 12px;border-radius:20px;border:0.5px solid #ddd8d2;background:#ffffff;font-size:11px;font-weight:600;color:#9ca3af;cursor:pointer;font-family:inherit;transition:all 0.12s;white-space:nowrap}
.status-chip:hover{border-color:#b5b0aa;color:#6b7280}
.status-chip.active{background:#1c1917;border-color:#1c1917;color:#ffffff}
.btn-primary{padding:8px 16px;border-radius:8px;background:#6366f1;color:#fff;border:none;font-size:13px;font-weight:600;cursor:pointer;transition:background 0.12s;text-decoration:none;display:inline-flex;align-items:center;gap:6px;font-family:inherit}
.btn-primary:hover{background:#4f46e5;text-decoration:none;color:#fff}
.btn-connect{display:inline-flex;align-items:center;gap:5px;padding:7px 14px;border-radius:8px;background:#6366f1;color:#fff;font-size:12px;font-weight:600;text-decoration:none;white-space:nowrap;transition:background 0.12s;font-family:inherit;border:none;cursor:pointer}
.btn-connect:hover{background:#4f46e5;text-decoration:none;color:#fff}
.pending-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#6366f1;display:flex;align-items:center;gap:6px;margin-bottom:10px}
.pending-dot{width:6px;height:6px;border-radius:50%;background:#6366f1;animation:pulse 1.5s infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
.feed-col{overflow-y:auto;min-height:0}
@media(max-width:768px){html,body{height:auto;overflow:auto}.sidebar{display:none}.main-content{height:auto;overflow:visible;padding-left:0!important}.nav-hamburger{display:flex!important}.topbar-search{flex:1;min-width:0}.pending-pill{font-size:10px;padding:3px 8px}.page-body{overflow:visible}.insight-panel{padding:16px 16px 16px}.cards-panel{padding:16px 16px 32px}}
</style>
</head>
<body>
{_SIDEBAR_}

<div class="main-content">
  {onboarding_banner}
  {reauth_banner}
  {new_accounts_banner}
  <div id="twofa-banner" style="display:none;padding:0 24px 0"></div>
  {welcome_state}

  <div class="topbar">
    <button class="nav-hamburger" onclick="openMobileDrawer()" aria-label="Open menu" style="display:none">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
    </button>
    <div class="topbar-search">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <input type="text" placeholder="Search accounts…" oninput="filterCards(this.value)" id="card-search">
    </div>
    <div style="flex:1"></div>
    {agent_status_indicator}
    <div id="pending-badge" style="display:{pending_display}" class="pending-pill">
      {pending_count} awaiting decision
    </div>
    <span id="global-sync-time" style="font-size:11px;color:#9ca3af;white-space:nowrap">{global_sync_label}</span>
    <button id="cloud-sync-btn" onclick="cloudSync()" class="btn-sync" title="Sync all accounts — fetches live data from connected sites, then re-extracts fields">
      <svg id="sync-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
      <span id="sync-label">Sync</span>
    </button>
  </div>

  <div id="mighty-toast"></div>

  <div class="page-body" {feed_col_hidden}>
    <input type="hidden" name="_csrf" value="{csrf_token}">

    <!-- Intelligence panel: greeting → available benefits → status -->
    <div class="insight-panel">
      <div class="insight-inner">
      {hero_section_html}
      {insights_html}
      {relevant_now_html}
      {progress_section_html}
      </div><!-- /insight-inner -->
      <script>
      (function() {
        var TYPE_ICONS = {
          'value_drop': '📉',
          'bill_increase': '📈',
          'credit_added': '💰',
          'expiry': '📅',
          'payment_due': '💳',
          'unused_credit': '💡'
        };
        function renderActionCenter(items, themesHtml) {
          themesHtml = themesHtml || '';
          var panel = document.getElementById('action-center-panel');
          var meta  = document.getElementById('action-center-meta');
          if (!panel) return;
          if (!items.length) {
            panel.innerHTML = '<div style="color:#6b7280;font-size:13px;padding:8px 0">✓ Nothing needs attention right now</div>';
            return;
          }
          if (meta) meta.textContent = items.length + ' item' + (items.length !== 1 ? 's' : '');
          var borderColors = {urgent:'#ef4444', soon:'#f59e0b', info:'#3b82f6', change:'#3b82f6'};
          var bgColors     = {urgent:'#fef2f2', soon:'#fffbeb', info:'#f0f9ff', change:'#f0f9ff'};
          var html = '';
          items.forEach(function(r) {
            var icon = TYPE_ICONS[r.type] || (r.urgency === 'urgent' ? '🚨' : r.urgency === 'soon' ? '⏰' : '💡');
            var border = borderColors[r.urgency] || '#e5e7eb';
            var bg     = bgColors[r.urgency]     || '#f9fafb';
            var days = r.days_left !== null && r.days_left !== undefined ? (r.days_left === 0 ? ' — today' : ' — ' + r.days_left + 'd') : '';
            var name = r.account_name || r.source || '';
            html += '<div style="background:' + bg + ';border-left:3px solid ' + border + ';border-radius:0 8px 8px 0;padding:10px 14px;display:flex;align-items:center;gap:10px;font-size:13px;margin-bottom:6px">'
              + '<span style="font-size:16px">' + icon + '</span>'
              + '<div style="flex:1"><strong style="color:#111">' + name + '</strong>'
              + '<span style="color:#6b7280;margin:0 4px">·</span>'
              + '<span style="color:#374151">' + r.message + '</span>'
              + (days ? '<span style="color:#9ca3af;font-size:12px">' + days + '</span>' : '')
              + '</div></div>';
          });
          panel.innerHTML = themesHtml + html;
        }
        async function loadActionCenter() {
          try {
            var remResp = fetch('/api/reminders');
            var summResp = fetch('/api/reminders/summary');
            var remData = await (await remResp).json();
            var summary = {};
            try { summary = await (await summResp).json(); } catch(e) { summary = {themes: [], total: 0}; }
            var reminders = remData.reminders || [];

            // Update hero attention count
            // Update action center meta
            var meta = document.getElementById('action-center-meta');
            if (meta) meta.textContent = summary.total > 0 ? summary.total + ' items' : 'All clear';

            // Render theme chips if multiple themes
            var themesHtml = '';
            if (summary.themes && summary.themes.length > 1) {
              themesHtml = '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">' +
                summary.themes.map(function(t) {
                  return '<div style="display:flex;align-items:center;gap:4px;padding:4px 10px;' +
                    'background:' + (t.urgent_count > 0 ? '#fef2f2' : '#f3f4f6') + ';' +
                    'border-radius:20px;font-size:12px;color:' + (t.urgent_count > 0 ? '#b91c1c' : '#374151') + '">' +
                    t.icon + ' ' + t.label + ' <strong>' + t.count + '</strong>' +
                    '</div>';
                }).join('') + '</div>';
            }

            var panel = document.getElementById('action-center-panel');
            if (!panel) return;

            if (reminders.length === 0) {
              panel.innerHTML = '<div style="color:#6b7280;font-size:13px;padding:8px 0">✓ Nothing needs attention right now</div>';
              return;
            }

            renderActionCenter(reminders, themesHtml);
          } catch(e) {
            console.error('Action center load error:', e);
            var panel = document.getElementById('action-center-panel');
            if (panel) panel.innerHTML = '<div style="color:#9ca3af;font-size:13px;padding:8px 0">Could not load.</div>';
          }
        }
        loadActionCenter();
      })();
      </script>
    </div><!-- /insight-panel -->

    <!-- Cards panel: account grid (evidence layer) -->
    <div class="cards-panel">
      <div class="cards-panel-inner">
      <div class="cards-panel-header">
        <span style="font-size:12px;font-weight:700;color:#9ca3af;text-transform:uppercase;letter-spacing:.07em">Accounts</span>
        <div style="display:flex;align-items:center;gap:8px">
          {agent_cta_button}
          <button class="btn-connect" onclick="openDashConnectModal()">+ Connect</button>
        </div>
      </div>

      <div id="expiring-banner" style="display:{expiring_display};align-items:center;gap:10px;background:#fffbeb;border:0.5px solid rgba(217,119,6,0.3);border-radius:10px;padding:10px 16px;margin-bottom:16px;cursor:pointer" data-base-display="{expiring_display}" onclick="toggleExpiringFilter(this)">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="2" style="flex-shrink:0"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
        <span style="font-size:13px;font-weight:600;color:#92400e"><span id="expiring-count">{expiring_count}</span> account{expiring_plural} with expiring benefits or upcoming due dates</span>
        <span style="font-size:11px;color:#b45309;margin-left:auto" id="expiring-filter-label">Click to highlight</span>
      </div>

      {account_data_html}

      <div id="fview-activity" style="display:none">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;flex-wrap:wrap">
        <input type="text" class="feed-search" id="feed-search" placeholder="Filter actions…" oninput="filterFeed(this.value)" style="flex:1;min-width:160px;margin-bottom:0">
        <div style="display:flex;gap:6px;flex-shrink:0" id="status-filters">
          <button class="status-chip active" onclick="setStatusFilter('all',this)">All</button>
          <button class="status-chip" onclick="setStatusFilter('pending',this)">Pending</button>
          <button class="status-chip" onclick="setStatusFilter('approved',this)">Approved</button>
          <button class="status-chip" onclick="setStatusFilter('denied',this)">Denied</button>
          <button class="status-chip" onclick="setStatusFilter('timed_out',this)">Timed out</button>
        </div>
      </div>
      <div id="feed">
        {feed_html}
        <div id="feed-no-results" style="display:none;padding:40px 0;text-align:center;color:#b8b2ac;font-size:13px">No matching actions</div>
      </div>
      </div><!-- /fview-activity -->
      </div><!-- /cards-panel-inner -->
    </div><!-- /cards-panel -->
  </div><!-- /page-body -->
</div><!-- /main-content -->


<script>
function filterCards(q) {
  q = q.toLowerCase();
  document.querySelectorAll('.acct-card').forEach(function(card) {
    var name = (card.dataset.name || '').toLowerCase();
    // Also search field labels + values (the visible text inside the card)
    var content = card.textContent.toLowerCase();
    card.style.display = (!q || name.includes(q) || content.includes(q)) ? '' : 'none';
  });
  // Hide/show category groups if all cards in them are hidden
  document.querySelectorAll('.cat-group').forEach(function(g) {
    var visible = Array.from(g.querySelectorAll('.acct-card')).some(function(c) { return c.style.display !== 'none'; });
    g.style.display = visible ? '' : 'none';
  });
}
function switchTab(name, btn) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}
function switchFeedTab(name, btn) {
  ['activity','accounts'].forEach(function(n) {
    document.getElementById('fview-' + n).style.display = n === name ? '' : 'none';
    document.getElementById('ftab-' + n).classList.toggle('active', n === name);
  });
  // Hide expiring banner on activity log — it only applies to account cards
  var banner = document.getElementById('expiring-banner');
  if (banner) banner.style.display = name === 'activity' ? 'none' : (banner.dataset.baseDisplay || 'none');
  // Reset status filter chips when leaving/entering activity tab
  if (name === 'accounts') {
    _activeStatusFilter = 'all';
    document.querySelectorAll('.status-chip').forEach(function(c) { c.classList.remove('active'); });
    var allChip = document.querySelector('.status-chip');
    if (allChip) allChip.classList.add('active');
  }
  sessionStorage.setItem('mighty-feed-tab', name);
}
// Always open on Account Data — don't persist activity log tab across page loads
(function() {
  var banner = document.getElementById('expiring-banner');
  if (banner) banner.dataset.baseDisplay = banner.style.display;
})();
function copyMcpConfig(btn) {
  navigator.clipboard.writeText(document.getElementById('mcpConfigBox').textContent);
  btn.textContent = 'Copied!';
  setTimeout(() => btn.textContent = 'Copy config', 1800);
}
function copyPrompt(btn) {
  navigator.clipboard.writeText(document.getElementById('promptBox').value);
  btn.textContent = 'Copied!';
  setTimeout(() => btn.textContent = 'Copy system prompt', 1800);
}
function decide(actionId, decision) {
  var card = document.getElementById("action-" + actionId);
  if (card) { card.querySelectorAll(".btn-authorize, .btn-reject").forEach(function(b) { b.disabled = true; }); }
  fetch('/dashboard/decide/' + actionId, {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({decision})
  }).then(() => location.reload());
}
// Restore scroll position after any reload — wait 300ms so async content (action center etc.)
// finishes painting before we set scrollTop, otherwise layout shifts reset it to 0.
(function() {
  var fc = document.querySelector('.feed-col');
  var saved = sessionStorage.getItem('mighty-feed-scroll');
  if (fc && saved) { fc.scrollTop = parseInt(saved); sessionStorage.removeItem('mighty-feed-scroll'); }
  var sy = sessionStorage.getItem('mighty-scroll-y');
  if (sy) {
    sessionStorage.removeItem('mighty-scroll-y');
    var target = parseInt(sy);
    if (!target) return;
    // Retry until the page is tall enough that scrollTop actually sticks.
    // A fixed timeout fails when content renders slowly — the browser silently
    // clamps scrollTop to 0 if the scroll height isn't large enough yet.
    var attempts = 0;
    function tryRestore() {
      var pb = document.querySelector('.page-body');
      if (!pb) { if (++attempts < 15) setTimeout(tryRestore, 100); return; }
      pb.scrollTop = target;
      if (pb.scrollTop < target - 5 && ++attempts < 15) {
        setTimeout(tryRestore, 150);
      }
    }
    setTimeout(tryRestore, 100);
  }
})();

function reloadWithScroll() {
  var fc = document.querySelector('.feed-col');
  if (fc) sessionStorage.setItem('mighty-feed-scroll', fc.scrollTop);
  // .main-content has overflow:hidden — actual scroll container is .page-body
  var pb = document.querySelector('.page-body');
  sessionStorage.setItem('mighty-scroll-y', pb ? pb.scrollTop : (window.scrollY || document.documentElement.scrollTop || 0));
  location.reload();
}

// ── 2FA pending challenges ────────────────────────────────────────────────────
function load2FAChallenges() {
  fetch('/api/2fa/pending').then(function(r){return r.json();}).then(function(d){
    if (!d.ok || !d.challenges.length) {
      document.getElementById('twofa-banner').style.display = 'none';
      return;
    }
    var banner = document.getElementById('twofa-banner');
    banner.style.display = 'block';
    var html = '';
    d.challenges.forEach(function(c) {
      var isCode = c.challenge_type === 'sms' || c.challenge_type === 'email_code';
      html += '<div style="background:#ffffff;border:1px solid #e8e4de;border-radius:10px;padding:14px 16px;margin-bottom:8px">'
        + '<div style="font-size:13px;font-weight:600;color:#1c1917;margin-bottom:4px">'
        + '🔐 ' + c.account_name + ' needs ' + (isCode ? 'a verification code' : 'push approval')
        + '</div>'
        + (c.message ? '<div style="font-size:12px;color:#6b7280;margin-bottom:8px">' + c.message + '</div>' : '')
        + (isCode
          ? '<div style="display:flex;gap:8px;align-items:center">'
            + '<input type="text" id="code-' + c.id + '" placeholder="Enter code" maxlength="8" '
            + 'style="padding:7px 10px;border:1.5px solid #e8e4de;border-radius:7px;font-size:13px;width:120px;outline:none;background:#ffffff;color:#1c1917">'
            + '<button data-cid="' + c.id + '" data-push="0" onclick="submit2FA(this.dataset.cid,this.dataset.push)" '
            + 'style="padding:7px 14px;background:#6366f1;color:#fff;border:none;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer">Submit</button>'
            + '</div>'
          : '<button data-cid="' + c.id + '" data-push="1" onclick="submit2FA(this.dataset.cid,this.dataset.push)" '
            + 'style="padding:7px 14px;background:#6366f1;color:#fff;border:none;border-radius:7px;font-size:12px;font-weight:600;cursor:pointer">I approved it ✓</button>'
        )
        + '</div>';
    });
    banner.innerHTML = '<div style="background:#fffbef;border:1px solid rgba(245,158,11,0.35);border-radius:12px;padding:14px 16px;margin-bottom:16px">'
      + '<div style="font-size:12px;font-weight:700;color:#b45309;margin-bottom:8px">⚠ 2FA approval needed</div>'
      + html + '</div>';
  });
}

function submit2FA(id, pushFlag) {
  var isPush = pushFlag === '1' || pushFlag === true;
  var code = isPush ? 'confirmed' : (document.getElementById('code-' + id) || {value:''}).value.trim();
  fetch('/api/2fa/respond/' + id, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({_csrf: document.querySelector('[name="_csrf"]').value, code: code, confirmed: isPush ? '1' : ''})
  }).then(function(r){return r.json();}).then(function(d){
    if (d.ok) { load2FAChallenges(); }
    else alert(d.error || 'Error');
  });
}

// Poll for 2FA challenges every 15s
setInterval(load2FAChallenges, 15000);
load2FAChallenges();

// Auto-reload if any account is still discovering fields (max 4 attempts).
// Skip if a sync is actively running — the sync poller will reload when it finishes.
if (document.querySelector('[data-discovering="1"]') && !window._syncPoll) {
  var _discoverReloads = parseInt(localStorage.getItem('mighty-discover-reloads') || '0');
  if (_discoverReloads < 4) {
    localStorage.setItem('mighty-discover-reloads', _discoverReloads + 1);
    var _discoverTimer = setTimeout(function() {
      // Double-check: if sync started after page load, skip this reload
      if (!window._syncPoll) reloadWithScroll();
    }, 12000);
  } else {
    // Retries exhausted — keep counter at 99 so we don't restart the cycle on next page load
    // (counter resets to 0 naturally when fields are successfully found and card loses data-discovering)
    localStorage.setItem('mighty-discover-reloads', '99');
    document.querySelectorAll('[data-discovering="1"]').forEach(function(el) {
      var card = el.closest('.acct-card');
      var cardName = card ? (card.getAttribute('data-name') || '') : '';
      var syncStatus = card ? (card.getAttribute('data-sync-status') || '') : '';
      var msg;
      if (syncStatus === 'login_required') {
        msg = '<div style="color:#92400e;background:#fef3c7;border-radius:6px;padding:10px 12px;font-size:13px;margin:0">⚠️ Log in to ' + (cardName || 'this site') + ' in Chrome to restore sync</div>';
      } else if (syncStatus === 'no_data') {
        msg = '<div style="color:#6b7280;font-size:13px;font-style:italic">Visit ' + (cardName || 'this site') + ' in Chrome to capture your account data</div>';
      } else {
        msg = '<span style="color:#9ca3af;font-size:12px;font-style:italic">No fields found — use ↻ to retry sync</span>';
      }
      el.innerHTML = msg;
    });
  }
} else {
  localStorage.removeItem('mighty-discover-reloads');
}

// Live-updating relative timestamps
function fmtRelative(ts) {
  try {
    var d = new Date(ts);
    var secs = Math.floor((Date.now() - d.getTime()) / 1000);
    if (secs < 60)  return 'just now';
    var mins = Math.floor(secs / 60);
    if (mins < 60)  return mins + ' minute' + (mins === 1 ? '' : 's') + ' ago';
    var hrs = Math.floor(mins / 60);
    if (hrs < 24)   return hrs + ' hour' + (hrs === 1 ? '' : 's') + ' ago';
    var days = Math.floor(hrs / 24);
    return days + ' day' + (days === 1 ? '' : 's') + ' ago';
  } catch(e) { return ''; }
}
function updateSyncTimes() {
  document.querySelectorAll('[data-synced]').forEach(function(el) {
    var ts = el.dataset.synced;
    if (!ts) return;
    // Login-required accounts carry a data-sync-status on the parent card
    var card = el.closest('[data-sync-status]');
    var syncStatus = card ? card.dataset.syncStatus : '';
    if (syncStatus === 'login_required') {
      el.innerHTML = '<span style="font-size:11px;color:#dc2626;font-weight:700">🔐 Login required</span>';
      return;
    }
    var rel = fmtRelative(ts);
    if (rel) {
      var color = '#22c55e', icon = '✓', fw = '500';
      var secs2 = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
      var hrs2 = secs2 / 3600;
      if (hrs2 >= 72) { color = '#dc2626'; icon = '!'; fw = '700'; }
      else if (hrs2 >= 48) { color = '#f59e0b'; icon = '~'; }
      else if (hrs2 >= 24) { color = '#f59e0b'; icon = '~'; }
      else if (hrs2 >= 2) { color = '#6b7280'; icon = '✓'; }
      el.innerHTML = '<span style="font-size:11px;color:' + color + ';font-weight:' + fw + '">' + icon + ' Synced ' + rel + '</span>';
    }
    try {
      var d = new Date(ts);
      el.title = d.toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'})
               + ' at ' + d.toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit'});
    } catch(e) {}
  });
}
updateSyncTimes();
setInterval(updateSyncTimes, 30000);

// Auto-sync + auto-discover on page load if stale or never synced
fetch('/sync/status').then(function(r){return r.json();}).then(function(s){
  if (s.running) return;
  var lastSync = s.last ? new Date(s.last) : null;
  var minsAgo = lastSync ? (Date.now() - lastSync.getTime()) / 60000 : Infinity;
  if (minsAgo > 180) {
    cloudSync();
  } else {
    // Still trigger auto-discovery for any account missing fields
    fetch('/credentials/auto-discover', {method:'POST',
      headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams({_csrf: document.querySelector('[name="_csrf"]').value || ''})
    }).catch(function(){});
  }
}).catch(function(){});

function _setSyncLabel(text) {
  var lbl = document.getElementById('sync-label');
  if (lbl) lbl.textContent = text;
}
function _triggerRescan(onDone) {
  fetch('/api/data/rediscover-all', {method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:'_csrf=' + encodeURIComponent(document.querySelector('input[name="_csrf"]') ?
      document.querySelector('input[name="_csrf"]').value : '')
  }).then(function(r){return r.json();}).catch(function(){}).finally(function(){
    if (onDone) onDone();
  });
}
function _showToast(msg, duration) {
  var t = document.getElementById('mighty-toast');
  if (!t) return;
  t.textContent = msg;
  t.className = 'show';
  setTimeout(function(){ t.className = 'hide'; }, (duration || 3000) - 200);
  setTimeout(function(){ t.className = ''; }, duration || 3000);
}
// Extension presence detection — dashboard_relay.js sends this on load
var _extPresent = false;
window.addEventListener('message', function(e) {
  if (e.data && e.data.type === '__mighty_ext_present__') _extPresent = true;
});

function _finishSync() {
  var btn = document.getElementById('cloud-sync-btn');
  document.querySelectorAll('.acct-card').forEach(function(c){ c.classList.remove('is-syncing'); });
  _setSyncLabel('Extracting…');
  _triggerRescan(function() {
    if (btn) { btn.classList.remove('syncing'); btn.disabled = false; }
    _setSyncLabel('Sync');
    sessionStorage.setItem('mighty-post-sync', '1');
    reloadWithScroll();
  });
}

// Server-side polling: fetch /api/latest-sync every 8s; when the timestamp changes
// to within the last 60s we know a fresh sync just landed and can reload.
function _startSyncPoller(baseline) {
  if (window._syncPoll) clearInterval(window._syncPoll);
  // Safety timeout: give up after 12 minutes and reload anyway
  var giveUp = setTimeout(function() {
    clearInterval(window._syncPoll);
    _finishSync();
  }, 720000);
  window._syncPoll = setInterval(function() {
    fetch('/api/latest-sync').then(function(r){ return r.json(); }).then(function(d) {
      if (!d.latest) return;
      var ts = new Date(d.latest);
      var secsAgo = (Date.now() - ts.getTime()) / 1000;
      // New sync landed if the timestamp is different from what we had before AND recent
      if (d.latest !== baseline && secsAgo < 120) {
        clearInterval(window._syncPoll);
        clearTimeout(giveUp);
        _finishSync();
      }
    }).catch(function() {});
  }, 8000);
}

function cloudSync() {
  var btn = document.getElementById('cloud-sync-btn');
  if (!btn) return;
  btn.classList.add('syncing');
  _setSyncLabel('Syncing…');
  btn.disabled = true;
  // Set sentinel immediately so checkForUpdates is blocked before the async fetch returns
  window._syncPoll = window._syncPoll || true;

  document.querySelectorAll('.acct-card').forEach(function(c) { c.classList.add('is-syncing'); });

  // Fetch current latest-sync baseline before triggering, so we can detect the change
  fetch('/api/latest-sync').then(function(r){ return r.json(); }).then(function(d) {
    var baseline = d.latest || null;
    if (_extPresent) {
      // Ask the extension to run sync, then poll the server for completion
      window.postMessage({type:'__mighty_dashboard__', action:'sync_now'}, '*');
      _startSyncPoller(baseline);
    } else {
      // Fallback: Railway cloud sync (for users without the extension)
      fetch('/sync/now', {method:'POST',
        headers:{'Content-Type':'application/x-www-form-urlencoded'},
        body:'_csrf=' + encodeURIComponent(document.querySelector('input[name="_csrf"]') ?
          document.querySelector('input[name="_csrf"]').value : '')
      }).then(function(r){return r.json();}).then(function(d2){
        if (d2.ok) {
          _startSyncPoller(baseline);
        } else { _finishSync(); }
      }).catch(_finishSync);
    }
  }).catch(function() {
    // If we can't even get baseline, fall back to extension trigger with blind polling
    if (_extPresent) {
      window.postMessage({type:'__mighty_dashboard__', action:'sync_now'}, '*');
      _startSyncPoller(null);
    } else { _finishSync(); }
  });
}

function resetFields(source) {
  var csrf = (document.querySelector('input[name="_csrf"]') || {}).value || '';
  fetch('/credentials/fields/reset/' + source, {
    method: 'POST',
    headers: {'X-CSRF-Token': csrf, 'Content-Type': 'application/json'}
  }).then(function(r){return r.json();}).then(function(d){
    if (d.ok) { reloadWithScroll(); }
    else { alert('Reset failed — try refreshing'); }
  }).catch(function(){ alert('Reset failed — try refreshing'); });
}

function toggleExpiringFilter(banner) {
  var active = banner.getAttribute('data-filter') === '1';
  var label = document.getElementById('expiring-filter-label');
  if (active) {
    banner.removeAttribute('data-filter');
    document.querySelectorAll('.acct-card').forEach(function(c){
      c.classList.remove('highlight-off');
      c.classList.remove('highlight-on');
    });
    if (label) label.textContent = 'Click to highlight';
  } else {
    banner.setAttribute('data-filter', '1');
    document.querySelectorAll('.acct-card').forEach(function(c){
      if (c.querySelector('.acct-alert')) {
        c.classList.remove('highlight-off');
        c.classList.add('highlight-on');
      } else {
        c.classList.add('highlight-off');
        c.classList.remove('highlight-on');
      }
    });
    if (label) label.textContent = 'Click to clear';
  }
}

function toggleExpand(btn) {
  var card = btn.closest('.acct-card');
  if (!card) return;
  var expanded = card.classList.toggle('is-expanded');
  var count = btn.getAttribute('data-count');
  var svg = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var svgUp = '<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 6l3-3 3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  if (expanded) {
    btn.innerHTML = svgUp + 'Show less';
  } else {
    btn.innerHTML = svg + count + ' more field' + (count == 1 ? '' : 's');
  }
}

function forceDiscover(source, btn) {
  var csrf = (document.querySelector('input[name="_csrf"]') || {}).value || '';
  var orig = btn.textContent;
  btn.textContent = '…';
  btn.disabled = true;
  fetch('/api/data/force-discover/' + source, {
    method: 'POST',
    headers: {'X-CSRF-Token': csrf}
  }).then(function(r){ return r.json(); }).then(function(d){
    if (d.ok) {
      btn.textContent = '✓';
      localStorage.removeItem('mighty-discover-reloads'); // allow discovery to retry
      setTimeout(function(){ reloadWithScroll(); }, 800);
    } else {
      btn.textContent = orig;
      btn.disabled = false;
      alert(d.error || 'Discovery failed');
    }
  }).catch(function(){
    btn.textContent = orig;
    btn.disabled = false;
    alert('Request failed — try again');
  });
}

function syncAccount(source, btn) {
  var csrf = (document.querySelector('input[name="_csrf"]') || {}).value || '';
  var orig = btn.innerHTML;
  btn.innerHTML = '…';
  btn.disabled = true;
  // Block checkForUpdates from triggering a scroll-resetting reload mid-sync
  window._syncPoll = window._syncPoll || true;
  fetch('/sync/account/' + source, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({_csrf: csrf})
  }).then(function(r) {
    if (!r.ok) {
      if (window._syncPoll === true) window._syncPoll = null;
      btn.innerHTML = orig; btn.disabled = false; return;
    }
    var poll = setInterval(function() {
      fetch('/sync/status').then(function(r2){ return r2.json(); }).then(function(s) {
        if (!s.running) {
          clearInterval(poll);
          window._syncPoll = null;
          reloadWithScroll();
        }
      });
    }, 2000);
    window._syncPoll = poll;
  }).catch(function() {
    if (window._syncPoll === true) window._syncPoll = null;
    btn.innerHTML = orig;
    btn.disabled = false;
  });
}

var _activeStatusFilter = 'all';
function setStatusFilter(status, btn) {
  _activeStatusFilter = status;
  document.querySelectorAll('.status-chip').forEach(function(b) { b.classList.remove('active'); });
  if (btn) btn.classList.add('active');
  applyFeedFilters();
}
function filterFeed(q) {
  applyFeedFilters();
}
function applyFeedFilters() {
  var q = (document.getElementById('feed-search') ? document.getElementById('feed-search').value : '').toLowerCase();
  var visible = 0;
  document.querySelectorAll('.action-card').forEach(function(card) {
    var textMatch = !q || card.textContent.toLowerCase().includes(q);
    var statusMatch = _activeStatusFilter === 'all';
    if (!statusMatch) {
      var badge = card.querySelector('.badge-approved,.badge-denied,.badge-pending,.badge-logged,.badge-timeout');
      if (badge) {
        var cls = badge.className;
        if (_activeStatusFilter === 'approved' && cls.includes('badge-approved')) statusMatch = true;
        if (_activeStatusFilter === 'denied' && cls.includes('badge-denied')) statusMatch = true;
        if (_activeStatusFilter === 'pending' && (cls.includes('badge-pending') || card.classList.contains('is-pending'))) statusMatch = true;
        if (_activeStatusFilter === 'timed_out' && cls.includes('badge-timeout')) statusMatch = true;
      }
    }
    var show = textMatch && statusMatch;
    card.style.display = show ? '' : 'none';
    if (show) visible++;
  });
  var noResults = document.getElementById('feed-no-results');
  if (noResults) noResults.style.display = visible === 0 ? '' : 'none';
}

function toggleDetail(id) {
  var el = document.getElementById('detail-' + id);
  if (!el) return;
  var open = el.style.display === 'none';
  el.style.display = open ? 'block' : 'none';
  var btn = document.getElementById('dtoggle-' + id);
  if (btn) btn.textContent = open ? 'details ↑' : 'details ↓';
}

var lastPending = document.querySelectorAll('.is-pending').length > 0;
function checkForUpdates() {
  // Don't interrupt an active sync — the sync poller handles the reload when done.
  // Check is also repeated inside the callback because sync can start while the
  // fetch is in-flight (~200ms), and we must not fire a bare location.reload() then.
  if (window._syncPoll) return;
  fetch('/dashboard/has-pending').then(function(r) { return r.json(); }).then(function(d) {
    if (window._syncPoll) return; // re-check: sync may have started while fetch was in-flight
    if (d.pending !== lastPending) {
      lastPending = d.pending; // update so we only reload once per state transition
      reloadWithScroll();
    }
  }).catch(function() {});
}
setInterval(checkForUpdates, 4000);
// Immediately check when user switches back to this tab
document.addEventListener('visibilitychange', function() {
  if (document.visibilityState === 'visible') checkForUpdates();
});

// Register SW for push delivery (notifications managed in /settings)
if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js');
}

function toggleHealth(btn, source) {
  var detail = btn.nextElementSibling;
  var chevron = btn.querySelector('.health-chevron');
  if (detail.style.display === 'none') {
    detail.style.display = 'block';
    chevron.textContent = '▾';
    if (!detail.dataset.loaded) {
      detail.dataset.loaded = '1';
      var _staticCoverage = detail.innerHTML; // preserve server-rendered coverage block
      detail.innerHTML = _staticCoverage + '<div style="color:#d1d5db;font-size:11px;margin-top:4px">Loading sync health…</div>';
      fetch('/api/sync-health/' + source).then(function(r){return r.json();}).then(function(h){
        var fa = h.failure_reason ? '<span style="color:#ef4444">✗ '+h.failure_reason+'</span>' : '<span style="color:#22c55e">✓ ok</span>';
        var _ca = h.confidence_avg || 0;
        var _clabel = _ca >= 0.85 ? 'High' : _ca >= 0.60 ? 'Medium' : _ca > 0 ? 'Needs review' : null;
        var conf = _clabel ? _clabel + ' confidence' : 'no confidence data';
        var cov  = h.coverage ? h.coverage.message+' ('+h.coverage.score+'/100)' : '';
        var gapHtml = '';
        if (h.gaps && h.gaps.count > 0) {
          var moreStr = h.gaps.more > 0 ? ' +'+h.gaps.more+' more' : '';
          gapHtml = '<div style="font-size:11px;color:#9ca3af;margin-top:3px">Missing: '+h.gaps.labels.join(', ')+moreStr+'</div>';
        } else if (h.gaps && h.gaps.count === 0 && h.field_count > 0) {
          gapHtml = '<div style="font-size:11px;color:#22c55e;margin-top:3px">All expected fields found &#10003;</div>';
        }
        var hint = h.coverage && h.coverage.hint ? '<div style="color:#f59e0b;margin-top:2px">⚠ '+h.coverage.hint+'</div>' : '';
        var api  = h.sources && h.sources.api > 0 ? ' · '+h.sources.api+' from API' : '';
        var changes = '';
        if (h.recent_changes && h.recent_changes.length) {
          changes = '<div style="margin-top:4px;color:#6b7280">Recent changes: '+h.recent_changes.slice(0,2).map(function(c){return c.field_label+': '+c.old_value+'→'+c.new_value;}).join(' · ')+'</div>';
        }
        // Fetch coverage pct from dedicated endpoint and augment display
        fetch('/api/coverage/' + source).then(function(cr){return cr.json();}).then(function(cv){
          var covLabel = '';
          if (cv.expected_count > 0) {
            covLabel = '<span style="color:#a3a3a3">Coverage '+cv.coverage_pct+'% ('+cv.found_count+'/'+cv.expected_count+' expected fields)</span>';
          } else {
            covLabel = '<span style="color:#a3a3a3">'+cv.found_count+' fields found</span>';
          }
          var _syncHtml = '<div style="border-top:1px solid #f3f4f6;padding-top:6px;margin-top:4px">'+fa+' · '+h.field_count+' fields · '+conf+api+'</div><div style="margin-top:2px">'+covLabel+'</div><div style="color:#9ca3af">'+cov+'</div>'+gapHtml+hint+changes;
          detail.innerHTML = _staticCoverage + _syncHtml;
        }).catch(function(){
          var _syncHtml = '<div style="border-top:1px solid #f3f4f6;padding-top:6px;margin-top:4px">'+fa+' · '+h.field_count+' fields · '+conf+api+'</div><div style="color:#9ca3af">'+cov+'</div>'+gapHtml+hint+changes;
          detail.innerHTML = _staticCoverage + _syncHtml;
        });
      }).catch(function(){ detail.innerHTML = _staticCoverage + '<div style="color:#ef4444;font-size:11px;margin-top:4px">Could not load sync health</div>'; });
    }
  } else {
    detail.style.display = 'none';
    chevron.textContent = '▶';
  }
}
function toggleSnippet(el) {
  var reveal = el.nextElementSibling;
  reveal.style.display = reveal.style.display === 'none' ? 'block' : 'none';
  el.textContent = reveal.style.display === 'none' ? 'Why?' : 'Hide';
}
</script>
{onboarding_modal}
{dash_modals}
</body>
</html>"""

ONBOARDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Get started — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:#f8f7f5;color:#1a1a1a;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:20px 24px}
.wrap{width:100%;max-width:480px;display:flex;flex-direction:column;min-height:calc(100vh - 40px)}
.logo{display:flex;align-items:center;gap:8px;justify-content:center;margin-bottom:16px;flex-shrink:0}
.logo-mark{width:28px;height:28px;display:flex;align-items:center;justify-content:center;background:#080a10;border-radius:6px;overflow:hidden}
.logo-mark img{height:28px;width:28px;object-fit:cover}
.logo-name{font-size:17px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.progress{display:flex;gap:6px;justify-content:center;margin-bottom:16px;flex-shrink:0}
.progress-dot{width:8px;height:8px;border-radius:50%;background:#e5e3df;transition:all 0.2s;padding:6px;margin:-6px;background-clip:content-box;cursor:default;opacity:0.35}
.progress-dot.active{background:#7c3aed;background-clip:content-box;opacity:1}
.progress-dot.done{background:#7c3aed;background-clip:content-box;cursor:pointer;opacity:0.45}
.progress-dot.done:hover{opacity:0.75}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:28px;box-shadow:0 4px 24px rgba(0,0,0,0.06);flex:1;min-height:0;overflow-y:auto;max-height:calc(100vh - 130px)}
.step{display:none}.step.active{display:block}
.step-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#7c3aed;margin-bottom:8px}
.step-title{font-size:21px;font-weight:700;color:#1a1a1a;margin-bottom:10px;line-height:1.3}
.step-sub{font-size:14px;color:#6b7280;line-height:1.6;margin-bottom:22px}
.btn{width:100%;padding:12px;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer;transition:all 0.12s}
.btn-primary{background:#7c3aed;color:#fff}.btn-primary:hover{background:#6d28d9}
.btn-copy{font-size:12px;font-weight:600;padding:6px 12px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;cursor:pointer;transition:background 0.12s;white-space:nowrap;flex-shrink:0}
.btn-copy:hover{background:#ede9fe}
.skip{text-align:center;margin-top:12px}
.skip a{font-size:12px;color:#9ca3af;text-decoration:none}.skip a:hover{color:#6b7280}
</style>
</head>
<body>
<div class="wrap">
  <a href="/" style="text-decoration:none" class="logo">
    <div class="logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
    <span class="logo-name">Mighty</span>
  </a>
  <div class="progress">
    <div class="progress-dot active" id="dot-0"></div>
    <div class="progress-dot" id="dot-1"></div>
    <div class="progress-dot" id="dot-2"></div>
  </div>
  <div class="card">

    <!-- Step 0: Welcome -->
    <div class="step active" id="step-0">
      <div class="step-label">Welcome</div>
      <div class="step-title">AI that asks before it acts</div>
      <div class="step-sub">Mighty puts approval checkpoints in your agent's path. You define what's consequential — the agent pauses and waits for your decision. Every action is logged, in the agent's own words.</div>
      <button class="btn btn-primary" onclick="goTo(1)">Begin setup →</button>
    </div>

    <!-- Step 1: Copy your prompt -->
    <div class="step" id="step-1">
      <div class="step-title">Add this to your AI tool</div>
      <div class="step-sub">Paste this into your ChatGPT Project instructions, Custom GPT, or any agent that supports a system prompt. It tells the agent to check with you before taking any consequential action.</div>
      <div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:20px">
        <textarea id="prompt-box" readonly style="flex:1;font-family:ui-monospace,monospace;font-size:11px;color:#374151;background:#f8f7f5;border:1.5px solid #e5e3df;border-radius:8px;padding:10px;height:200px;resize:none;overflow:auto;outline:none"></textarea>
        <button class="btn-copy" id="copy-btn" onclick="copyPrompt(this)">Copy</button>
      </div>
      <button class="btn btn-primary" onclick="goTo(2)">I've pasted it — continue →</button>
      <div class="skip"><a href="/onboarding/skip">Skip to dashboard</a></div>
    </div>

    <!-- Step 2: All done -->
    <div class="step" id="step-2">
      <div style="text-align:center;padding:8px 0 24px">
        <div style="font-size:44px;margin-bottom:16px">✅</div>
        <div class="step-title" style="text-align:center;margin-bottom:12px">You're all set</div>
        <div class="step-sub" style="text-align:center;margin-bottom:0">Open your AI tool and ask it to do something consequential — like send an email or book a meeting. Mighty will pause it and ask for your approval first.</div>
      </div>
      <a href="/onboarding/skip" class="btn btn-primary" style="margin-top:8px;display:block;text-align:center;text-decoration:none">Go to my dashboard →</a>
    </div>

  </div>
</div>
<script type="application/json" id="__mighty_onboarding_data__">MIGHTY_ONBOARDING_DATA</script>
<script>
var currentStep = 0;

function goTo(n) {
  document.getElementById('step-' + currentStep).classList.remove('active');
  currentStep = n;
  document.getElementById('step-' + n).classList.add('active');
  for (var i = 0; i <= 2; i++) {
    var dot = document.getElementById('dot-' + i);
    if (!dot) continue;
    dot.classList.remove('active', 'done');
    if (i < n) dot.classList.add('done');
    else if (i === n) dot.classList.add('active');
  }
}

function copyPrompt(btn) {
  var el = document.getElementById('prompt-box');
  navigator.clipboard.writeText(el.value);
  btn.textContent = 'Copied!';
  setTimeout(function() { btn.textContent = 'Copy'; }, 1800);
}

var _d = JSON.parse(document.getElementById('__mighty_onboarding_data__').textContent);
var API_KEY  = _d.api_key;
var BASE_URL = _d.base_url;

(function() {
  var prompt = (
    "MIGHTY AUTHORIZATION — follow every session.\\n\\n"
    + "Before any consequential action (emails, purchases, file edits, calendar changes, external API calls):\\n\\n"
    + "POST " + BASE_URL + "/api/authorize\\n"
    + '  {"api_key":"' + API_KEY + '","action_type":"<type>","label":"<what you are about to do>","fields":[["Key","Value"]]}\\n\\n'
    + "Then poll GET " + BASE_URL + "/api/status/<request_id> every 3 seconds until status is resolved.\\n"
    + "approved → proceed | denied or timeout → stop and tell the user\\n\\n"
    + "For routine, non-destructive actions (searches, reads, lookups):\\n"
    + "POST " + BASE_URL + "/api/record\\n"
    + '  {"api_key":"' + API_KEY + '","action_type":"<type>","label":"<what you did>","outcome":"completed"}'
  );
  var p = document.getElementById('prompt-box');
  if (p) p.value = prompt;
})();
</script>
</body>
</html>"""

SETTINGS_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Settings — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
html,body{height:100%;overflow:hidden}
body{display:flex;flex-direction:row}
.main-content{flex:1;min-width:0;height:100vh;overflow-y:auto}
.page-wrap{max-width:600px;margin:0 auto;padding:32px 36px;display:flex;flex-direction:column;gap:16px}
.page-title{font-size:20px;font-weight:700;color:#1c1917;margin-bottom:4px}
.card{background:#ffffff;border:1px solid #e8e4de;border-radius:12px;padding:24px;box-shadow:0 1px 2px rgba(0,0,0,0.05),0 4px 16px rgba(0,0,0,0.06)}
.section-title{font-size:11px;font-weight:700;color:#9ca3af;margin-bottom:16px;text-transform:uppercase;letter-spacing:0.7px}
.toggle-row{display:flex;align-items:flex-start;gap:12px;padding:12px 0}
.toggle-row+.toggle-row{border-top:1px solid #f5f2ed}
.toggle-label{font-size:13px;font-weight:600;color:#1c1917;margin-bottom:3px}
.toggle-hint{font-size:12px;color:#6b7280;line-height:1.5}
.api-key-wrap{display:flex;align-items:center;gap:8px;margin-top:4px}
.api-key-val{flex:1;font-family:ui-monospace,monospace;font-size:12px;color:#6b7280;background:#f5f2ed;border:1px solid #e8e4de;border-radius:6px;padding:8px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn-sm{font-size:12px;font-weight:600;padding:6px 12px;background:#ffffff;color:#6366f1;border:1px solid #e8e4de;border-radius:6px;white-space:nowrap;cursor:pointer;transition:all 0.12s;font-family:inherit}
.btn-sm:hover{background:#f5f2ed;border-color:#6366f1}
.push-status{font-size:12px;color:#6b7280;margin-top:6px;min-height:16px}
.push-btn{font-size:12px;font-weight:600;padding:6px 12px;background:#ffffff;color:#6366f1;border:1px solid #e8e4de;border-radius:6px;cursor:pointer;transition:all 0.12s;display:none;margin-top:6px;font-family:inherit}
.push-btn:hover{background:#f5f2ed;border-color:#6366f1}
.btn-danger{font-size:12px;font-weight:600;padding:8px 16px;background:transparent;color:#dc2626;border:1px solid rgba(220,38,38,0.2);border-radius:6px;cursor:pointer;transition:all 0.12s;text-align:center;font-family:inherit;width:100%}
.btn-danger:hover{background:rgba(220,38,38,0.06);border-color:#dc2626}
.btn-danger-severe{background:#dc2626;color:#fff;border-color:#dc2626}
.btn-danger-severe:hover{background:#b91c1c;border-color:#b91c1c;color:#fff}
.btn-settings-primary{font-size:13px;font-weight:600;padding:8px 16px;background:#6366f1;color:#fff;border:none;border-radius:6px;cursor:pointer;transition:background 0.12s;font-family:inherit}
.btn-settings-primary:hover{background:#4f46e5}
.ntfy-link{display:inline-block;margin-top:6px;font-size:12px;font-family:ui-monospace,monospace;color:#6366f1;background:#f5f2ed;border:1px solid #e8e4de;border-radius:6px;padding:6px 10px;text-decoration:none;word-break:break-all}
.ntfy-link:hover{border-color:#6366f1;text-decoration:none}
.toggle-row input[type=checkbox]{-webkit-appearance:none;appearance:none;width:16px;height:16px;border:1.5px solid #e8e4de;border-radius:4px;background:#ffffff;cursor:pointer;position:relative;flex-shrink:0;margin-top:2px;transition:border-color 0.12s,background 0.12s}
.toggle-row input[type=checkbox]:checked{background:#6366f1;border-color:#6366f1}
.toggle-row input[type=checkbox]:checked::after{content:\'\';position:absolute;left:3px;top:1px;width:5px;height:9px;border:2px solid #fff;border-top:none;border-left:none;transform:rotate(45deg)}
.settings-input{width:100%;padding:10px 14px;border:1.5px solid #e8e4de;border-radius:8px;font-size:13px;font-family:inherit;outline:none;color:#1c1917;background:#ffffff;transition:border-color 0.12s}
.settings-input:focus{border-color:#6366f1}
.settings-input::placeholder{color:#c0bbb5}
.settings-label{display:block;font-size:11px;font-weight:600;color:#9ca3af;margin-bottom:6px;letter-spacing:0.3px;text-transform:uppercase}
@media(max-width:768px){html,body{height:auto;overflow:auto}.sidebar{display:none}.main-content{height:auto;overflow:visible;padding-left:0!important}.nav-hamburger{display:flex!important}.topbar-search{flex:1;min-width:0}}
</style>
</head>
<body>
{_SIDEBAR_}

<div class="main-content">
<div style="display:none;align-items:center;gap:10px;padding:12px 16px;border-bottom:0.5px solid rgba(0,0,0,0.07);background:#eee9e2;position:sticky;top:0;z-index:2" id="mobile-topbar-settings">
  <button class="nav-hamburger" onclick="openMobileDrawer()" aria-label="Open menu" style="display:none">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
  </button>
  <span style="font-size:15px;font-weight:700;color:#1c1917">Settings</span>
</div>
<script>(function(){var t=document.getElementById('mobile-topbar-settings');if(t&&window.innerWidth<=768)t.style.display='flex';})();</script>
<div class="page-wrap">
  <div class="page-title">Settings</div>

  <div class="card">
    <div class="section-title">Notifications</div>
    <div class="toggle-row">
      <input type="checkbox" id="notif-push" {push_checked} onchange="save()">
      <div>
        <div class="toggle-label">Browser alerts</div>
        <div class="toggle-hint">Desktop popup when your agent needs a decision.</div>
        <div id="push-status" class="push-status"></div>
        <button id="push-enable-btn" class="push-btn" onclick="enablePush()">Enable browser notifications</button>
      </div>
    </div>
    <div class="toggle-row">
      <input type="checkbox" id="notif-ntfy" {ntfy_checked} onchange="save()">
      <div>
        <div class="toggle-label">Mobile alerts (ntfy)</div>
        <div class="toggle-hint">Install the free <a href="https://ntfy.sh" target="_blank">ntfy app</a>, then subscribe to your channel on your phone.</div>
        <a href="https://ntfy.sh/{ntfy_topic}" target="_blank" class="ntfy-link">ntfy.sh/{ntfy_topic} ↗</a>
        <div style="font-size:11px;color:#9ca3af;margin-top:6px">Only action labels and approval links are sent — no account data.</div>
      </div>
    </div>
    <div class="toggle-row">
      <input type="checkbox" id="notif-email" {email_checked} onchange="onEmailToggle()">
      <div>
        <div class="toggle-label">Email alerts</div>
        <div class="toggle-hint">Receive an email when your agent requests approval.</div>
        <div id="email-notif-warn" style="display:{postmark_warn};margin-top:6px;font-size:12px;color:#fbbf24;background:rgba(251,191,36,0.08);border:1px solid rgba(251,191,36,0.2);border-radius:6px;padding:6px 10px;line-height:1.5">Email alerts require the POSTMARK_API_KEY environment variable to be set on your server.</div>
      </div>
    </div>
    <div class="toggle-row">
      <input type="checkbox" id="notif-expiry" {alert_expiry_checked} onchange="saveExpiryPref()">
      <div>
        <div class="toggle-label">Benefit expiry alerts</div>
        <div class="toggle-hint">Get an email before certificates or credits are about to expire — at 30, 14, and 7 days.</div>
      </div>
    </div>
    <div style="display:flex;align-items:center;justify-content:flex-end;margin-top:4px">
      <span id="save-ind" style="font-size:11px;color:#34d399;display:none">Saved ✓</span>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Account</div>
    <div style="font-size:13px;color:#8892a4;margin-bottom:16px">Signed in as <span style="color:#1c1917;font-weight:600">{email}</span></div>
    <label class="settings-label">What should we call you?</label>
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:20px">
      <input type="text" id="preferred-name" placeholder="Your first name" class="settings-input" style="margin-bottom:0;flex:1" value="{preferred_name}" maxlength="40">
      <button class="btn-settings-primary" onclick="saveName()" style="white-space:nowrap">Save name</button>
      <span id="name-msg" style="font-size:12px;color:#34d399;display:none">Saved ✓</span>
    </div>
    <label class="settings-label">Change email address</label>
    <input type="email" id="email-new" placeholder="New email address" class="settings-input" style="margin-bottom:10px">
    <input type="password" id="email-pw" placeholder="Confirm with current password" class="settings-input" style="margin-bottom:10px">
    <div style="display:flex;align-items:center;gap:12px">
      <button class="btn-settings-primary" onclick="changeEmail()">Update email</button>
      <span id="email-msg" style="font-size:12px;display:none"></span>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Privacy</div>
    <div class="toggle-row">
      <input type="checkbox" id="minimal-logging" {minimal_logging_checked} onchange="savePrivacy()">
      <div>
        <div class="toggle-label">Minimal logging</div>
        <div class="toggle-hint">Store only action type and timestamp — not labels or field details. Reduces what Mighty can see, but makes your activity log less useful.</div>
        <span id="privacy-ind" style="display:none;font-size:12px;color:#34d399;margin-top:4px">Saved</span>
      </div>
    </div>
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid #f5f2ed">
      <label style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;margin-bottom:12px">
        <input type="checkbox" id="delete-raw-after-extract" {delete_raw_checked} onchange="saveDeleteRaw()" style="margin-top:2px">
        <div>
          <div style="font-size:13px;font-weight:500;color:#374151">Delete raw page text after extraction</div>
          <div style="font-size:12px;color:#6b7280;margin-top:1px">Raw page text is discarded immediately after fields are extracted. Saves storage and reduces exposure.</div>
        </div>
      </label>
      <div style="margin-top:12px">
        <a href="/privacy/audit-log" style="font-size:13px;color:#3b82f6;text-decoration:none">
          View audit log &rarr;
        </a>
        <span style="color:#d1d5db;margin:0 8px">|</span>
        <a href="/privacy/domains" style="font-size:13px;color:#3b82f6;text-decoration:none">
          Manage captured domains &rarr;
        </a>
      </div>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Connection</div>
    <div style="font-size:12px;color:#8892a4;margin-bottom:8px">Your API key — used to connect your agent to Mighty. Keep it secret.</div>
    <div class="api-key-wrap">
      <div class="api-key-val" id="apiKeyVal">{api_key_masked}</div>
      <button class="btn-sm" id="reveal-key-btn" onclick="toggleRevealKey(this)">Reveal</button>
      <button class="btn-sm" onclick="copyKey(this)">Copy</button>
    </div>
    <div style="font-size:11px;color:#9ca3af;margin-top:8px">Anyone with this key can submit actions on your behalf.</div>
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid #f5f2ed;display:flex;align-items:center;gap:10px;flex-wrap:wrap">
      <a href="/onboarding" style="display:inline-block;padding:8px 14px;background:#f5f2ed;color:#6366f1;border:1px solid #e8e4de;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none">Re-run setup</a>
      <a href="/extension-setup" target="_blank" style="display:inline-block;padding:8px 14px;background:#f0fdf4;color:#059669;border:1px solid #bbf7d0;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none">🔌 Setup Chrome Extension</a>
      <span style="font-size:11px;color:#9ca3af">Opens a page the extension reads to auto-configure itself</span>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Security</div>
    <div style="font-size:13px;color:#8892a4;margin-bottom:16px">Change your account password.</div>
    <label class="settings-label">Current password</label>
    <input type="password" id="pw-current" placeholder="Your current password" class="settings-input" style="margin-bottom:12px">
    <label class="settings-label">New password</label>
    <input type="password" id="pw-new" placeholder="At least 6 characters" class="settings-input" style="margin-bottom:12px">
    <label class="settings-label">Confirm new password</label>
    <input type="password" id="pw-confirm" placeholder="Repeat new password" class="settings-input" style="margin-bottom:12px">
    <div style="display:flex;align-items:center;gap:12px">
      <button class="btn-settings-primary" onclick="changePassword()">Update password</button>
      <span id="pw-msg" style="font-size:12px;display:none"></span>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Extension notifications</div>
    <div style="font-size:12px;color:#8892a4;margin-bottom:16px">Control when the Mighty extension surfaces benefits while you browse.</div>
    <div style="display:flex;flex-direction:column;gap:10px" id="notif-pref-wrap" data-current="{notification_pref}">
      <label class="toggle-row" style="cursor:pointer;align-items:flex-start;gap:12px;padding:10px 0">
        <input type="radio" name="notif_pref" value="quiet" style="margin-top:3px;flex-shrink:0;accent-color:#6366f1">
        <div>
          <div class="toggle-label">Quiet pill</div>
          <div class="toggle-hint">Small indicator in the corner — expand to see benefits.</div>
        </div>
      </label>
      <label class="toggle-row" style="cursor:pointer;align-items:flex-start;gap:12px;padding:10px 0">
        <input type="radio" name="notif_pref" value="checkout" style="margin-top:3px;flex-shrink:0;accent-color:#6366f1">
        <div>
          <div class="toggle-label">Checkout only</div>
          <div class="toggle-hint">Surface benefits only on booking and payment pages.</div>
        </div>
      </label>
      <label class="toggle-row" style="cursor:pointer;align-items:flex-start;gap:12px;padding:10px 0">
        <input type="radio" name="notif_pref" value="expiring" style="margin-top:3px;flex-shrink:0;accent-color:#6366f1">
        <div>
          <div class="toggle-label">Expiring only</div>
          <div class="toggle-hint">Only show benefits that expire within 30 days.</div>
        </div>
      </label>
      <label class="toggle-row" style="cursor:pointer;align-items:flex-start;gap:12px;padding:10px 0">
        <input type="radio" name="notif_pref" value="never" style="margin-top:3px;flex-shrink:0;accent-color:#6366f1">
        <div>
          <div class="toggle-label">Never</div>
          <div class="toggle-hint">Don't surface benefits while browsing.</div>
        </div>
      </label>
    </div>
    <div style="display:flex;align-items:center;justify-content:flex-end;margin-top:4px">
      <span id="notif-pref-ind" style="font-size:11px;color:#34d399;display:none">Saved ✓</span>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Account discovery</div>
    <div style="font-size:13px;color:#6b7280;margin-bottom:14px;line-height:1.5">
      Scan your email inbox to find accounts you might want to add to Mighty. Only sender addresses are checked — no email content is read.
    </div>
    <a href="/email-scan" class="btn-sm" style="display:inline-block;text-decoration:none">🔍 Find accounts from email</a>
  </div>

  <div class="card">
    <div class="section-title">Data &amp; Privacy</div>
    <button class="btn-sm" onclick="window.location.href=\'/settings/export-csv\'">↓ Export activity log (CSV)</button>
    <hr style="border:none;border-top:1px solid #f5f2ed;margin:16px 0">
    <div style="font-size:11px;font-weight:700;color:#f87171;margin-bottom:10px;text-transform:uppercase;letter-spacing:0.5px">Danger zone</div>
    <div style="display:flex;flex-direction:column;gap:6px">
      <span style="font-size:12px;color:#9ca3af">Clears your action history. Your account and credentials stay active.</span>
      <button class="btn-danger" id="del-activity-btn" onclick="deleteActivity()">Delete all activity</button>
      <span id="del-activity-msg" style="font-size:12px;color:#34d399;display:none">All activity deleted.</span>
    </div>
    <div style="background:rgba(220,38,38,0.04);border:1px solid rgba(220,38,38,0.15);border-radius:8px;padding:14px;margin-top:8px">
      <div style="font-size:13px;font-weight:600;color:#dc2626;margin-bottom:4px">Permanently delete account</div>
      <div style="font-size:12px;color:#9ca3af;margin-bottom:12px;line-height:1.5">Deletes your account and all data immediately. This cannot be undone.</div>
      <div id="del-acct-btn-wrap">
        <button class="btn-danger btn-danger-severe" onclick="showDelConfirm()">Delete my account</button>
      </div>
    </div>
    <div id="del-acct-confirm" style="display:none">
      <label class="settings-label" style="margin-top:12px">Confirm with your current password</label>
      <input type="password" id="del-acct-pw" placeholder="Your password" class="settings-input" style="border-color:rgba(248,113,113,0.35);margin-bottom:10px">
      <div style="display:flex;gap:8px">
        <button class="btn-danger" style="flex:1" onclick="deleteAccount()">Confirm deletion</button>
        <button onclick="hideDelConfirm()" style="padding:8px 14px;background:#f5f2ed;border:1px solid #e8e4de;border-radius:6px;font-size:13px;font-weight:600;color:#8892a4;cursor:pointer;font-family:inherit">Cancel</button>
      </div>
      <div id="del-acct-err" style="font-size:12px;color:#f87171;margin-top:8px;display:none"></div>
    </div>
  </div>
</div>
</div>


<script>
var CSRF = '{csrf_token}';
var swReg = null;
if ('serviceWorker' in navigator && 'PushManager' in window) {
  navigator.serviceWorker.register('/sw.js').then(function(reg) {
    swReg = reg;
    reg.pushManager.getSubscription().then(function(sub) {
      var status = document.getElementById('push-status');
      var btn = document.getElementById('push-enable-btn');
      if (sub) {
        if (status) { status.textContent = 'Active ✓'; status.style.color = '#16a34a'; }
        if (btn) btn.style.display = 'none';
      } else if (Notification.permission === 'denied') {
        if (status) status.textContent = 'Blocked — allow in browser settings to enable.';
        if (btn) btn.style.display = 'none';
      } else {
        if (btn) btn.style.display = 'inline-block';
      }
    });
  });
}
var POSTMARK_CONFIGURED = {postmark_js};
function onEmailToggle() {
  var warn = document.getElementById('email-notif-warn');
  var cb = document.getElementById('notif-email');
  if (warn) warn.style.display = (cb.checked && !POSTMARK_CONFIGURED) ? 'block' : 'none';
  save();
}
function save() {
  fetch('/dashboard/notifications', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      ntfy: document.getElementById('notif-ntfy').checked,
      push: document.getElementById('notif-push').checked,
      email: document.getElementById('notif-email').checked
    })
  }).then(function() {
    var ind = document.getElementById('save-ind');
    if (ind) { ind.style.display = 'inline'; setTimeout(function() { ind.style.display = 'none'; }, 2000); }
  }).catch(function() {});
}
function saveExpiryPref() {
  var enabled = document.getElementById('notif-expiry').checked;
  fetch('/settings/alert-expiry', {
    method: 'POST',
    headers: {'Content-Type':'application/json','X-CSRF-Token': CSRF},
    body: JSON.stringify({enabled: enabled})
  }).then(function() {
    var ind = document.getElementById('save-ind');
    if (ind) { ind.style.display = 'inline'; setTimeout(function() { ind.style.display = 'none'; }, 2000); }
  }).catch(function() {});
}
function savePrivacy() {
  fetch('/settings/privacy', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({minimal_logging: document.getElementById('minimal-logging').checked})
  }).then(function() {
    var ind = document.getElementById('privacy-ind');
    if (ind) { ind.style.display = 'inline'; setTimeout(function() { ind.style.display = 'none'; }, 2000); }
  }).catch(function() {});
}
function saveDeleteRaw() {
  fetch('/settings/privacy', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({
      minimal_logging: document.getElementById('minimal-logging').checked,
      delete_raw_after_extract: document.getElementById('delete-raw-after-extract').checked
    })
  }).then(function() {
    var ind = document.getElementById('privacy-ind');
    if (ind) { ind.style.display = 'inline'; setTimeout(function() { ind.style.display = 'none'; }, 2000); }
  }).catch(function() {});
}
function initNotifPref() {
  var wrap = document.getElementById('notif-pref-wrap');
  if (!wrap) return;
  var current = wrap.getAttribute('data-current') || 'quiet';
  document.querySelectorAll('input[name="notif_pref"]').forEach(function(el) {
    if (el.value === current) el.checked = true;
    el.addEventListener('change', function() {
      var csrf = (document.querySelector('input[name="_csrf"]') || {}).value || '';
      fetch('/api/settings/notifications', {
        method: 'POST',
        headers: {'Content-Type':'application/json','X-CSRF-Token': csrf},
        body: JSON.stringify({pref: this.value})
      }).then(function() {
        var ind = document.getElementById('notif-pref-ind');
        if (ind) { ind.style.display = 'inline'; setTimeout(function() { ind.style.display = 'none'; }, 2000); }
      }).catch(function() {});
    });
  });
}
initNotifPref();
function saveName() {
  var name = (document.getElementById('preferred-name').value || '').trim();
  var msg = document.getElementById('name-msg');
  fetch('/settings/name', {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({_csrf: CSRF, preferred_name: name})
  }).then(r => r.json()).then(d => {
    msg.style.display = 'inline';
    msg.style.color = d.ok ? '#34d399' : '#f87171';
    msg.textContent = d.ok ? 'Saved ✓' : (d.error || 'Error');
    if (d.ok) setTimeout(() => { msg.style.display = 'none'; }, 2500);
  });
}
function changeEmail() {
  var newEmail = (document.getElementById('email-new').value || '').trim();
  var pw = document.getElementById('email-pw').value;
  var msg = document.getElementById('email-msg');
  if (!newEmail || !pw) { msg.textContent = 'Please fill in both fields.'; msg.style.color='#dc2626'; msg.style.display='inline'; return; }
  fetch('/settings/change-email', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({email: newEmail, password: pw})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      msg.textContent = 'Email updated. Page will reload…'; msg.style.color='#16a34a'; msg.style.display='inline';
      setTimeout(function() { location.reload(); }, 1500);
    } else {
      msg.textContent = d.error || 'Update failed.'; msg.style.color='#dc2626'; msg.style.display='inline';
    }
  }).catch(function() { msg.textContent = 'Network error.'; msg.style.color='#dc2626'; msg.style.display='inline'; });
}
function enablePush() {
  if (!swReg) return;
  var status = document.getElementById('push-status');
  var btn = document.getElementById('push-enable-btn');
  if (status) status.textContent = 'Setting up…';
  if (btn) btn.style.display = 'none';
  fetch('/api/push/vapid-public-key').then(function(r) { return r.json(); }).then(function(d) {
    var converted = urlB64ToUint8Array(d.key);
    swReg.pushManager.getSubscription().then(function(e) {
      return e ? e.unsubscribe() : Promise.resolve(true);
    }).then(function() {
      return swReg.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:converted});
    }).then(function(sub) {
      return fetch('/api/push/subscribe', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({subscription:sub.toJSON()})});
    }).then(function() {
      if (status) { status.textContent = 'Active ✓'; status.style.color = '#16a34a'; }
      if (btn) btn.style.display = 'none';
    }).catch(function(e) {
      if (Notification.permission === 'denied') {
        if (status) status.textContent = 'Blocked — allow in browser settings.';
      } else {
        if (status) status.textContent = 'Could not enable: ' + e.message;
        if (btn) btn.style.display = 'inline-block';
      }
    });
  });
}
var _fullApiKey = null;
function _fetchApiKey(cb) {
  if (_fullApiKey) { cb(_fullApiKey); return; }
  fetch('/api/my-key').then(function(r){ return r.json(); }).then(function(d){
    _fullApiKey = d.key || '';
    cb(_fullApiKey);
  }).catch(function(){ cb(''); });
}
function copyKey(btn) {
  _fetchApiKey(function(val) {
    navigator.clipboard.writeText(val);
    btn.textContent = 'Copied!';
    setTimeout(function() { btn.textContent = 'Copy'; }, 1800);
  });
}
var _keyRevealed = false;
function toggleRevealKey(btn) {
  var el = document.getElementById('apiKeyVal');
  _keyRevealed = !_keyRevealed;
  if (_keyRevealed) {
    _fetchApiKey(function(val) { el.textContent = val; });
    btn.textContent = 'Hide';
    btn.title = 'Hide API key';
  } else {
    // Re-mask using the cached key length if available
    var masked = _fullApiKey
      ? (_fullApiKey.slice(0,3) + '•'.repeat(Math.max(0, _fullApiKey.length - 3)))
      : el.textContent; // already masked from server render
    el.textContent = masked;
    btn.textContent = 'Reveal';
    btn.title = 'Show API key';
  }
}
function changePassword() {
  var cur = document.getElementById('pw-current').value;
  var nw  = document.getElementById('pw-new').value;
  var cnf = document.getElementById('pw-confirm').value;
  var msg = document.getElementById('pw-msg');
  if (!cur || !nw || !cnf) { msg.textContent = 'Please fill in all three fields.'; msg.style.color='#dc2626'; msg.style.display='inline'; return; }
  if (nw.length < 6) { msg.textContent = 'New password must be at least 6 characters.'; msg.style.color='#dc2626'; msg.style.display='inline'; return; }
  if (nw !== cnf) { msg.textContent = 'New passwords do not match.'; msg.style.color='#dc2626'; msg.style.display='inline'; return; }
  fetch('/settings/change-password', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({current: cur, password: nw})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      msg.textContent = 'Password updated.'; msg.style.color='#16a34a'; msg.style.display='inline';
      document.getElementById('pw-current').value = '';
      document.getElementById('pw-new').value = '';
      document.getElementById('pw-confirm').value = '';
      setTimeout(function() { msg.style.display='none'; }, 3000);
    } else {
      msg.textContent = d.error || 'Incorrect current password.'; msg.style.color='#dc2626'; msg.style.display='inline';
    }
  }).catch(function() { msg.textContent = 'Network error.'; msg.style.color='#dc2626'; msg.style.display='inline'; });
}
function urlB64ToUint8Array(b) {
  var pad = '='.repeat((4 - b.length % 4) % 4);
  var base64 = (b + pad).replace(/-/g,'+').replace(/_/g,'/');
  var raw = atob(base64); var out = new Uint8Array(raw.length);
  for (var i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}
function deleteActivity() {
  if (!confirm("This will permanently delete your entire activity log. This cannot be undone.")) return;
  var btn = document.getElementById('del-activity-btn');
  if (btn) btn.disabled = true;
  fetch('/settings/delete-activity', {method: 'POST'}).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) {
      var msg = document.getElementById('del-activity-msg');
      if (msg) { msg.style.display = 'inline'; setTimeout(function() { msg.style.display = 'none'; }, 3000); }
    } else {
      if (btn) btn.disabled = false;
    }
  }).catch(function() { if (btn) btn.disabled = false; });
}
function showDelConfirm() {
  document.getElementById('del-acct-btn-wrap').style.display = 'none';
  document.getElementById('del-acct-confirm').style.display = 'block';
  document.getElementById('del-acct-pw').focus();
}
function hideDelConfirm() {
  document.getElementById('del-acct-btn-wrap').style.display = 'block';
  document.getElementById('del-acct-confirm').style.display = 'none';
  document.getElementById('del-acct-pw').value = '';
  document.getElementById('del-acct-err').style.display = 'none';
}
function deleteAccount() {
  var pw = document.getElementById('del-acct-pw').value;
  var errEl = document.getElementById('del-acct-err');
  if (!pw) { errEl.textContent = 'Please enter your password to confirm.'; errEl.style.display = 'block'; return; }
  fetch('/settings/delete-account', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({password: pw})
  }).then(function(r) { return r.json(); }).then(function(d) {
    if (d.ok) { window.location.href = '/'; }
    else { errEl.textContent = d.error || 'Incorrect password.'; errEl.style.display = 'block'; }
  }).catch(function() { errEl.textContent = 'Network error — please try again.'; errEl.style.display = 'block'; });
}
</script>
</body>
</html>"""

APPROVE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<meta name="theme-color" content="#7c3aed">
<title>Authorize action — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;background:#f8f7f5}
.wrap{width:100%;max-width:480px}
.brand{display:flex;align-items:center;gap:8px;margin-bottom:20px;justify-content:center}
.brand-mark{width:28px;height:28px;display:flex;align-items:center;justify-content:center;background:#080a10;border-radius:6px;overflow:hidden}
.brand-mark img{height:28px;width:28px;object-fit:cover}
.brand-name{font-size:16px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.08)}
.card-header{background:#f5f3ff;border-bottom:1px solid #e9d5ff;padding:16px 20px;display:flex;align-items:center;gap:10px}
.card-header-dot{width:8px;height:8px;border-radius:50%;background:#7c3aed;animation:pulse 1.5s infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.card-header-text{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:1px;color:#5b21b6}
.card-headline{font-size:18px;font-weight:700;color:#1a1a1a;padding:18px 20px 4px;line-height:1.4}
.card-type{font-size:12px;color:#9ca3af;padding:0 20px 16px;font-family:ui-monospace,monospace}
.card-fields{padding:0 20px 16px;display:flex;flex-direction:column;gap:10px;border-bottom:1px solid #f0ede8}
.field-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#9ca3af;margin-bottom:2px}
.field-value{font-size:13px;color:#1a1a1a;line-height:1.5;word-break:break-word}
.card-actions{padding:16px 20px;display:flex;gap:10px}
.btn-approve{flex:1;padding:16px;background:#16a34a;color:#fff;border:none;border-radius:8px;font-size:16px;font-weight:700;transition:background 0.12s;touch-action:manipulation}
.btn-approve:hover{background:#15803d}
.btn-approve:active{transform:scale(0.98)}
.btn-deny{flex:1;padding:16px;background:#fff;color:#dc2626;border:2px solid #fecaca;border-radius:8px;font-size:16px;font-weight:700;transition:all 0.12s;touch-action:manipulation}
.btn-deny:hover{background:#fef2f2}
.btn-deny:active{transform:scale(0.98)}
.outcome{text-align:center;padding:28px 20px;font-size:15px;font-weight:600}
.outcome.approved{color:#16a34a;background:#f0fdf4}
.outcome.denied{color:#dc2626;background:#fef2f2}
.outcome.timeout{color:#9ca3af;background:#f9fafb}
.timeout-note{font-size:12px;color:#9ca3af;text-align:center;padding:10px 20px 16px;line-height:1.5}
</style>
</head>
<body>
<div class="wrap">
  <div class="brand">
    <div class="brand-mark">
      <img src="/logo-icon.png" alt="Mighty">
    </div>
    <span class="brand-name">Mighty</span>
  </div>
  <div class="card">
    {body}
  </div>
  <div id="agent-waiting-note" style="text-align:center;margin-top:16px;font-size:12px;color:#9ca3af">Your AI agent is waiting for this decision.</div>
</div>
</body>
</html>"""


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/logo-icon.png")
def logo_icon():
    import base64
    from flask import Response
    return Response(base64.b64decode(LOGO_ICON_B64), mimetype="image/png",
                    headers={"Cache-Control": "public, max-age=31536000"})

@app.route("/")
def landing():
    if "user_id" in session:
        return redirect("/dashboard")
    return LANDING_HTML

@app.route("/signup", methods=["GET"])
def signup_page():
    if "user_id" in session:
        return redirect("/dashboard")
    return SIGNUP_HTML.replace("{error}", "").replace("{csrf_token}", get_csrf_token())

@app.route("/signup", methods=["POST"])
def signup():
    if not _rate_limit(request.remote_addr, "signup", limit=5):
        err = '<div class="err">Too many attempts. Please wait a minute and try again.</div>'
        return SIGNUP_HTML.replace("{error}", err).replace("{csrf_token}", get_csrf_token()), 429
    check_csrf()
    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    if not email or "@" not in email or not password or len(password) < 6 or len(password) > 128:
        err = '<div class="err">Please enter a valid email and a password (6–128 characters).</div>'
        return SIGNUP_HTML.replace("{error}", err).replace("{csrf_token}", get_csrf_token())
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        err = '<div class="err">An account with that email already exists. <a href="/login">Sign in</a></div>'
        return SIGNUP_HTML.replace("{error}", err).replace("{csrf_token}", get_csrf_token())
    uid = secrets.token_hex(16)
    key = "mk_" + secrets.token_hex(20)
    db.execute(
        "INSERT INTO users (id,email,password_hash,api_key,created_at) VALUES (?,?,?,?,?)",
        (uid, email, hash_pw(password), key, iso()),
    )
    db.commit()
    session.permanent  = True
    session["user_id"] = uid
    session["email"]   = email
    return redirect("/onboarding")

@app.route("/enterprise-interest", methods=["POST"])
def enterprise_interest():
    data    = request.get_json(force=True)
    name    = data.get("name", "").strip()
    email   = data.get("email", "").strip()
    company = data.get("company", "").strip()
    message = data.get("message", "").strip()
    if not name or not email:
        return jsonify({"error": "name and email required"}), 400
    if len(message) > 4000:
        message = message[:4000]
    db = get_db()
    if not db.execute("SELECT 1 FROM enterprise_leads WHERE email=?", (email,)).fetchone():
        db.execute(
            "INSERT INTO enterprise_leads (id,name,email,company,message,created_at) VALUES (?,?,?,?,?,?)",
            (secrets.token_hex(12), name, email, company, message, iso())
        )
        db.commit()
    return jsonify({"ok": True})

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        if request.args.get("reset") == "1":
            info = '<div style="font-size:13px;color:#16a34a;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:7px;padding:9px 12px;margin-bottom:14px">Password updated successfully. Sign in with your new password.</div>'
            return LOGIN_HTML.replace("{error}", info).replace("{csrf_token}", get_csrf_token())
        return LOGIN_HTML.replace("{error}", "").replace("{csrf_token}", get_csrf_token())
    # Rate limit: 10 attempts per minute per IP
    if not _rate_limit(request.remote_addr, "login", limit=10):
        err = '<div class="err">Too many attempts. Please wait a minute and try again.</div>'
        return LOGIN_HTML.replace("{error}", err).replace("{csrf_token}", get_csrf_token()), 429
    check_csrf()
    email    = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")
    row = get_db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row or not check_pw(row["password_hash"], password):
        err = '<div class="err">Incorrect email or password.</div>'
        return LOGIN_HTML.replace("{error}", err).replace("{csrf_token}", get_csrf_token())
    session.permanent  = True
    session["user_id"] = row["id"]
    session["email"]   = row["email"]
    # Lazy migration: re-hash SHA-256 passwords to bcrypt on login
    if row["password_hash"] and ":" in row["password_hash"] and not row["password_hash"].startswith("$2"):
        get_db().execute("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(password), row["id"]))
        get_db().commit()
    nxt = request.form.get("next", "").strip()
    if nxt and nxt.startswith("/") and not nxt.startswith("//"):
        return redirect(nxt)
    return redirect("/dashboard")

@app.route("/logout", methods=["GET", "POST"])
def logout():
    if request.method == "GET":
        session.clear()
        return redirect("/login")
    check_csrf()
    session.clear()
    return redirect("/")

@app.route("/openapi-chatgpt.json")
def openapi_spec_chatgpt():
    """Minimal single-action schema for ChatGPT Custom GPTs.
    Only exposes mighty_log_decision to avoid multiple confirmation dialogs.
    """
    url = base_url()
    spec = {
        "openapi": "3.1.0",
        "info": {"title": "Mighty", "version": "1.0.0",
                 "description": "Log actions and approval decisions."},
        "servers": [{"url": url}],
        "paths": {
            "/api/log-decision": {
                "post": {
                    "operationId": "mighty_log_decision",
                    "summary": "Log an action and the user's approval decision",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["api_key", "action_type", "label", "decision"],
                                    "properties": {
                                        "api_key":     {"type": "string"},
                                        "action_type": {"type": "string"},
                                        "label":       {"type": "string"},
                                        "fields":      {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                                        "decision":    {"type": "string", "enum": ["approved", "denied"]}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Logged"}}
                }
            }
        }
    }
    return json.dumps(spec), 200, {"Content-Type": "application/json"}


@app.route("/openapi.json")
def openapi_spec():
    url = base_url()
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Mighty",
            "version": "1.0.0",
            "description": "Request human approval before consequential actions, and log routine actions."
        },
        "servers": [{"url": url}],
        "paths": {
            "/api/authorize": {
                "post": {
                    "operationId": "mighty_authorize",
                    "summary": "Request approval before a consequential action",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["api_key", "action_type", "label"],
                                    "properties": {
                                        "api_key": {"type": "string", "description": "Your Mighty API key"},
                                        "action_type": {"type": "string", "description": "Short category e.g. email, purchase, file_edit"},
                                        "label": {"type": "string", "description": "Human-readable description of what you are about to do"},
                                        "fields": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Optional context e.g. [[\"To\", \"alice@example.com\"]]"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Request created",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "request_id": {"type": "string"},
                                            "status": {"type": "string"},
                                            "approval_url": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/status/{request_id}": {
                "get": {
                    "operationId": "mighty_status",
                    "summary": "Poll for approval decision",
                    "parameters": [{"name": "request_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                    "responses": {
                        "200": {
                            "description": "Current status",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {"status": {"type": "string"}}
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/log-decision": {
                "post": {
                    "operationId": "mighty_log_decision",
                    "summary": "Log an action together with the user's approval decision in one step",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["api_key", "action_type", "label", "decision"],
                                    "properties": {
                                        "api_key":     {"type": "string", "description": "Your Mighty API key"},
                                        "action_type": {"type": "string", "description": "Short category e.g. email, purchase, file_edit"},
                                        "label":       {"type": "string", "description": "Human-readable description of the action"},
                                        "fields":      {"type": "array", "items": {"type": "array", "items": {"type": "string"}}, "description": "Key/value context e.g. [[\"To\", \"alice@example.com\"]]"},
                                        "decision":    {"type": "string", "enum": ["approved", "denied"], "description": "The user's decision"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {
                            "description": "Decision recorded",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "status":    {"type": "string"},
                                            "record_id": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "/api/record": {
                "post": {
                    "operationId": "mighty_record",
                    "summary": "Log a routine action silently",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["api_key", "action_type", "label"],
                                    "properties": {
                                        "api_key": {"type": "string"},
                                        "action_type": {"type": "string"},
                                        "label": {"type": "string"},
                                        "outcome": {"type": "string"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Logged"}}
                }
            }
        }
    }
    return json.dumps(spec), 200, {"Content-Type": "application/json"}

@app.route("/privacy")
def privacy():
    return PRIVACY_HTML

@app.route("/tos")
def tos():
    return TOS_HTML

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "GET":
        return FORGOT_HTML.replace("{message}", "").replace("{csrf_token}", get_csrf_token())
    if not _rate_limit(request.remote_addr, "forgot", limit=5):
        success = '<div class="info">If an account exists for that email, a reset link is on its way. Check your inbox (and spam folder).</div>'
        return FORGOT_HTML.replace("{message}", success).replace("{csrf_token}", get_csrf_token())
    check_csrf()
    email = request.form.get("email", "").strip().lower()
    # Always show success message — prevents user enumeration
    success = '<div class="info">If an account exists for that email, a reset link is on its way. Check your inbox (and spam folder).</div>'
    if email:
        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            db.execute("INSERT INTO password_resets (token, user_id, created_at) VALUES (?,?,?)",
                       (token, user["id"], iso()))
            db.commit()
            reset_url = f"{base_url()}/reset-password/{token}"
            send_password_reset_email(email, reset_url)
    return FORGOT_HTML.replace("{message}", success).replace("{csrf_token}", get_csrf_token())

@app.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    db = get_db()
    cutoff = (utcnow() - timedelta(hours=1)).isoformat()
    row = db.execute(
        "SELECT * FROM password_resets WHERE token=? AND used=0 AND created_at > ?",
        (token, cutoff)
    ).fetchone()
    if not row:
        err = '<div class="err">This reset link is invalid or has expired. <a href="/forgot-password">Request a new one</a>.</div>'
        return RESET_HTML.replace("{error}", err).replace("{csrf_token}", get_csrf_token())
    if request.method == "GET":
        return RESET_HTML.replace("{error}", "").replace("{csrf_token}", get_csrf_token())
    check_csrf()
    password = request.form.get("password", "")
    confirm  = request.form.get("confirm", "")
    if len(password) < 6:
        return RESET_HTML.replace("{error}", '<div class="err">Password must be at least 6 characters.</div>').replace("{csrf_token}", get_csrf_token())
    if password != confirm:
        return RESET_HTML.replace("{error}", '<div class="err">Passwords do not match. Please try again.</div>').replace("{csrf_token}", get_csrf_token())
    db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(password), row["user_id"]))
    db.execute("UPDATE password_resets SET used=1 WHERE token=?", (token,))
    db.commit()
    return redirect("/login?reset=1")


# ── Dashboard ─────────────────────────────────────────────────────────────────

def build_prompt(api_key, url):
    return (
        f"MIGHTY AUTHORIZATION — follow every session.\n\n"
        f"Your Mighty API key: \"{api_key}\"\n\n"
        f"When the user asks you to do anything consequential (emails, purchases, file edits, deletions, external API calls):\n"
        f"1. Ask: \"Shall I proceed?\"\n"
        f"2. If yes: call mighty_log_decision ONCE with:\n"
        f"   - api_key=\"{api_key}\"\n"
        f"   - action_type: the category (e.g. \"email\", \"purchase\", \"file_edit\", \"deletion\", \"api_call\")\n"
        f"   - label: a plain-English description of the specific action (e.g. \"Send email to john@example.com re: project update\")\n"
        f"   - fields: the FULL content being acted on as key-value pairs — for email include [[\"To\", \"<recipient>\"], [\"Subject\", \"<subject>\"], [\"Body\", \"<full email body>\"]]; for purchases include amount, merchant, item; include whatever a person would need to verify exactly what was done\n"
        f"   - decision=\"approved\"\n"
        f"   Then carry out the action.\n"
        f"3. If no: call mighty_log_decision ONCE with the same fields and decision=\"denied\" — then stop.\n\n"
        f"Call mighty_log_decision exactly once per action. Never call it before asking. Never call it more than once.\n"
        f"The fields you submit are the permanent record of what was approved — include enough detail that it could be verified later."
    )

def call_claude_for_prompt(description, api_key, url):
    """Call Claude Haiku to generate a tailored checkpoint prompt from an agent description."""
    system = (
        "You generate concise system prompt instructions for AI agents that tell them when to call "
        "the Mighty authorization API. Given a description of what an agent does, produce checkpoint "
        "instructions that list the specific action types requiring authorization.\n\n"
        "Return a JSON object with exactly two fields:\n"
        "- \"prompt\": string — the complete checkpoint instructions, concise and specific to this agent\n"
        "- \"warning\": string or null — null if the description was specific enough; a short plain-English "
        "message (1 sentence) if the description was too vague to generate useful checkpoints\n\n"
        "The prompt must include:\n"
        "1. A specific list of action types derived from the agent's description\n"
        "2. The exact API call format using the provided api_key and url\n"
        "3. Brief instructions for polling and handling approved/denied/timeout responses\n\n"
        "Keep the prompt under 120 words. Return JSON only, no markdown fences."
    )
    user_msg = (
        f"Agent description: {description}\n"
        f"API key: {api_key}\n"
        f"Mighty URL: {url}\n\n"
        f"API endpoints:\n"
        f"  Authorize: POST {url}/api/authorize\n"
        f"    body: {{\"api_key\":\"{api_key}\",\"action_type\":\"<type>\",\"label\":\"<desc>\",\"fields\":[[\"Key\",\"Val\"]]}}\n"
        f"  Poll status: GET {url}/api/status/<request_id>  →  approved | denied | pending | timeout\n"
        f"  Record (no approval): POST {url}/api/record\n"
        f"    body: {{\"api_key\":\"{api_key}\",\"action_type\":\"<type>\",\"label\":\"<desc>\",\"outcome\":\"completed\"}}"
    )
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 512,
        "system": system,
        "messages": [{"role": "user", "content": user_msg}]
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01"
        }
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())
    text = result["content"][0]["text"].strip()
    if text.startswith("```"):
        text = "\n".join(text.split("\n")[1:])
        if text.endswith("```"):
            text = text[:-3].strip()
    return json.loads(text)

def build_mcp_config(api_key, url):
    return (
        '{\n'
        '  "mcpServers": {\n'
        '    "mighty": {\n'
        '      "command": "python3",\n'
        '      "args": ["/Users/YOUR_USERNAME/mighty_mcp.py"],\n'
        '      "env": {\n'
        f'        "MIGHTY_API_KEY": "{api_key}",\n'
        f'        "MIGHTY_BASE_URL": "{url}"\n'
        '      }\n'
        '    }\n'
        '  }\n'
        '}'
    )

def build_feed_html(actions, base):
    if not actions:
        return '''<div class="empty-state">
  <div class="empty-state-icon">
    <svg width="20" height="20" viewBox="0 0 20 20" fill="none"><path d="M10 2L3 6v8l7 4 7-4V6l-7-4z" stroke="#7c3aed" stroke-width="1.5" stroke-linejoin="round"/><path d="M10 2v12M3 6l7 4 7-4" stroke="#7c3aed" stroke-width="1.5" stroke-linejoin="round"/></svg>
  </div>
  <div class="empty-state-title">No actions yet</div>
  <div class="empty-state-sub">When your agent logs an action or requests approval,<br>it will appear here.</div>
</div>'''
    html = []
    pending = [a for a in actions if a["status"] == "pending"]
    rest    = [a for a in actions if a["status"] != "pending"]
    if pending:
        html.append('<div class="pending-section">')
        html.append('<div class="pending-label"><div class="pending-dot"></div>Awaiting your decision</div>')
        for a in pending:
            html.append(action_card_html(a, base, show_buttons=True))
        html.append('</div>')
    for a in rest:
        html.append(action_card_html(a, base, show_buttons=False))
    return "\n".join(html)

def action_card_html(a, base, show_buttons):
    badge = STATUS_BADGE.get(a["status"], "")
    # Consequence level badge (only show if not routine)
    clevel = ""
    try:
        lvl = a["consequence_level"] if "consequence_level" in a.keys() else "routine"
    except Exception:
        lvl = "routine"
    if lvl and lvl != "routine":
        clevel = f'<span class="clevel-{he(lvl)}">{he(lvl.title())}</span>'
    # Fields
    fields_html = ""
    if a["fields"]:
        try:
            flist = json.loads(a["fields"])
            for k, v in flist:
                val = v if isinstance(v, str) else json.dumps(v)
                fields_html += f'<div class="field-row"><span class="field-key">{he(k)}</span><span class="field-val">{he(val)}</span></div>'
        except Exception:
            pass
    pending_cls = " is-pending" if a["status"] == "pending" else ""
    btns = ""
    if show_buttons:
        btns = f'''<div class="action-buttons">
          <button class="btn-authorize" onclick="decide('{he(a["id"])}','approve')">Approve</button>
          <button class="btn-reject"    onclick="decide('{he(a["id"])}','deny')">Deny</button>
        </div>'''
    aid = he(a["id"])
    # Expandable detail section
    extra = []
    if a["decided_at"]:
        extra.append(f'<span style="color:#9ca3af">Decided {fmt_time(a["decided_at"])}</span>')
    if a["outcome"]:
        extra.append(f'<span style="color:#9ca3af">Outcome: {he(str(a["outcome"]))}</span>')
    detail_html = ""
    if extra:
        detail_html = (
            f'<div id="detail-{aid}" style="display:none;padding:8px 16px 12px;border-top:1px solid #f0ede8;background:#f8f7f5">'
            + "".join(f'<span style="font-size:11px;color:#6b7280;border-radius:4px;padding:2px 0;margin-right:12px;display:inline-block">{e}</span>' for e in extra)
            + '</div>'
        )
    detail_toggle = (
        f'<span style="color:#d1d5db;margin:0 5px">·</span>'
        f'<button id="dtoggle-{aid}" onclick="toggleDetail(\'{aid}\')" style="font-size:11px;color:#6366f1;'
        'background:none;border:none;cursor:pointer;padding:0;font-weight:600;'
        'text-decoration:underline;text-underline-offset:2px">details ↓</button>'
        if extra else ""
    )
    return f'''<div class="action-card{pending_cls}" id="action-{aid}">
      <div class="action-top">
        <div style="min-width:0;flex:1">
          <div class="action-label">{he(a["label"])}</div>
          <div class="action-type">{he(a["action_type"])}{detail_toggle}</div>
        </div>
        <div class="action-badges">
          {clevel}
          {badge}
          <div class="action-time">{fmt_time(a["created_at"])}</div>
        </div>
      </div>
      {'<div class="action-fields">' + fields_html + '</div>' if fields_html else ''}
      {detail_html}
      {btns}
    </div>'''

# ── Module-level date regex (mirrors the one in _post_filter_fields) ─────────
import re as _re_mod
_MOD_DATE_RE = _re_mod.compile(
    r'\b(\d{4}-\d{2}-\d{2}'
    r'|\d{1,2}/\d{1,2}/\d{4}'
    r'|\d{1,2}[A-Za-z]{3}\d{4}'
    r'|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}'
    r')\b',
    _re_mod.IGNORECASE,
)

def _mod_normalise_date_str(s: str) -> str:
    _month_map = {
        "january": "Jan", "february": "Feb", "march": "Mar", "april": "Apr",
        "may": "May", "june": "Jun", "july": "Jul", "august": "Aug",
        "september": "Sep", "sept": "Sep", "october": "Oct",
        "november": "Nov", "december": "Dec",
    }
    norm = s.strip()
    for long, short in _month_map.items():
        norm = _re_mod.sub(long, short, norm, flags=_re_mod.IGNORECASE)
    return norm


def _parse_date_for_reminder(s: str):
    """Parse a date string for reminder computation. Returns date or None."""
    from datetime import datetime as _dt3
    s = _mod_normalise_date_str(s.strip())
    fmts = [
        "%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y",
        "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y",
        "%d%b%Y",
    ]
    for fmt in fmts:
        for variant in (s, s.upper()):
            try:
                return _dt3.strptime(variant, fmt).date()
            except ValueError:
                pass
    return None


# ── Reminder urgency tiers ────────────────────────────────────────────────────
_REMINDER_URGENT_DAYS = 7
_REMINDER_SOON_DAYS   = 30

def _get_reminders(uid: str) -> list:
    """Scan all account fields for actionable reminders.
    Returns list of dicts: {source, account_name, label, value, message, urgency, days_left}
    urgency: 'urgent' (≤7d) | 'soon' (≤30d) | 'info'
    """
    from datetime import datetime as _dt2
    reminders = []
    today = _dt2.utcnow().date()

    rows = get_db().execute(
        "SELECT source, display_name, data_enc FROM account_data WHERE user_id=?", (uid,)
    ).fetchall()

    for row in rows:
        source = row["source"]
        name   = row["display_name"]
        try:
            data  = decrypt_account_data(uid, row["data_enc"] or "")
            items = data.get("items") or data.get("ai_items") or []
        except Exception:
            continue

        for item in items:
            label = item.get("label", "")
            value = str(item.get("value", ""))
            lbl_l = label.lower()
            val_l = value.lower()

            # ── Expiry / valid-through dates ──────────────────────────────
            _EXPIRY_LABELS = ("expir", "valid through", "valid until", "expires", "book by", "fly by", "use by", "exp ")
            if any(e in lbl_l or e in val_l for e in _EXPIRY_LABELS):
                date_m = _MOD_DATE_RE.search(value + " " + label)
                if date_m:
                    try:
                        d = _parse_date_for_reminder(date_m.group(0))
                        if d and d >= today:
                            days = (d - today).days
                            urgency = "urgent" if days <= _REMINDER_URGENT_DAYS else ("soon" if days <= _REMINDER_SOON_DAYS else None)
                            if urgency:
                                reminders.append({
                                    "source": source, "account_name": name,
                                    "label": label, "value": value,
                                    "message": f"{label}: {value}",
                                    "urgency": urgency, "days_left": days,
                                    "expires_on": d.isoformat(),
                                })
                    except Exception:
                        pass

            # ── Payment / bill due dates ──────────────────────────────────
            _DUE_LABELS = ("due", "payment due", "bill due", "amount due", "minimum payment")
            if any(dl in lbl_l for dl in _DUE_LABELS):
                date_m = _MOD_DATE_RE.search(value + " " + label)
                if date_m:
                    try:
                        d = _parse_date_for_reminder(date_m.group(0))
                        if d and d >= today:
                            days = (d - today).days
                            if days <= 14:
                                urgency = "urgent" if days <= 3 else "soon"
                                reminders.append({
                                    "source": source, "account_name": name,
                                    "label": label, "value": value,
                                    "message": f"{label}: {value}",
                                    "urgency": urgency, "days_left": days,
                                    "expires_on": d.isoformat(),
                                })
                    except Exception:
                        pass

            # ── Unused credits above threshold ────────────────────────────
            _CREDIT_LABELS = ("credit remaining", "remaining credit", "travel credit", "dining credit",
                               "statement credit", "ecredit", "e-credit", "cashback", "cash back")
            if any(c in lbl_l for c in _CREDIT_LABELS):
                amount_m = re.search(r'\$\s*(\d+(?:\.\d{1,2})?)', value)
                if amount_m:
                    try:
                        amount = float(amount_m.group(1))
                        if amount >= 10:
                            reminders.append({
                                "source": source, "account_name": name,
                                "label": label, "value": value,
                                "message": f"{label}: {value}",
                                "urgency": "info", "days_left": None,
                                "expires_on": None,
                            })
                    except Exception:
                        pass

    # Sort: urgent first, then soon, then info; within tier sort by days_left
    def _sort_key(r):
        tier = {"urgent": 0, "soon": 1, "info": 2}.get(r["urgency"], 3)
        return (tier, r["days_left"] if r["days_left"] is not None else 999)

    reminders.sort(key=_sort_key)
    return reminders


def _get_change_alerts(uid: str) -> list[dict]:
    """Generate smart alerts from field_history: value drops, balance changes, new credits."""
    alerts = []
    try:
        db = get_db()
        # Get recent changes in the last 30 days
        rows = db.execute(
            "SELECT source, field_label, old_value, new_value, changed_at "
            "FROM field_history WHERE user_id=? AND changed_at >= datetime('now','-30 days') "
            "ORDER BY changed_at DESC LIMIT 100",
            (uid,)
        ).fetchall()

        for r in rows:
            old_v = r["old_value"] or ""
            new_v = r["new_value"] or ""
            label = r["field_label"]
            source = r["source"]
            changed_at = r["changed_at"]

            # Try to detect numeric changes (dollars, points)
            import re as _re
            def _extract_number(s):
                s = s.replace(",", "")
                m = _re.search(r'[\$£€]?\s*([\d]+(?:\.\d+)?)', s)
                return float(m.group(1)) if m else None

            old_n = _extract_number(old_v)
            new_n = _extract_number(new_v)

            if old_n is not None and new_n is not None and old_n != new_n:
                diff = new_n - old_n
                pct = abs(diff / old_n * 100) if old_n else 0

                if diff < 0 and pct >= 5:
                    # Significant decrease
                    urgency = "urgent" if pct >= 20 else "info"
                    alerts.append({
                        "type": "value_drop",
                        "urgency": urgency,
                        "source": source,
                        "label": label,
                        "message": f"{label} dropped from {old_v} to {new_v}",
                        "detail": f"{abs(diff):.0f} decrease ({pct:.0f}%)",
                        "changed_at": changed_at,
                    })
                elif diff > 0 and pct >= 5:
                    # Significant increase — positive for credits/points, negative for bills
                    credit_keywords = ["credit", "point", "mile", "reward", "cashback", "bonus"]
                    bill_keywords = ["bill", "due", "charge", "amount due", "balance due"]

                    label_lower = label.lower()
                    if any(k in label_lower for k in bill_keywords):
                        urgency = "soon" if pct >= 15 else "info"
                        alerts.append({
                            "type": "bill_increase",
                            "urgency": urgency,
                            "source": source,
                            "label": label,
                            "message": f"{label} increased from {old_v} to {new_v}",
                            "detail": f"{diff:.0f} increase ({pct:.0f}%)",
                            "changed_at": changed_at,
                        })
                    elif any(k in label_lower for k in credit_keywords):
                        alerts.append({
                            "type": "credit_added",
                            "urgency": "info",
                            "source": source,
                            "label": label,
                            "message": f"{label} increased from {old_v} to {new_v}",
                            "detail": f"+{diff:.0f} ({pct:.0f}% increase)",
                            "changed_at": changed_at,
                        })
    except Exception:
        pass
    return alerts


def _source_category(source: str) -> str:
    """Return display category for a source key."""
    for cat_key, schema in _CATEGORY_SCHEMAS.items():
        if source in schema["sources"]:
            cat_map = {
                "travel_loyalty": "Travel",
                "credit_card":    "Finance",
                "utilities":      "Utilities",
                "subscription":   "Subscriptions",
                "banking":        "Finance",
                "health":         "Health",
                "shopping":       "Shopping",
                "insurance":      "Insurance",
                "automotive":     "Automotive",
            }
            return cat_map.get(cat_key, "Other")
    if source.startswith("custom_"):
        return "Custom"
    return "Other"


def _coverage_score(source: str, field_count: int) -> dict:
    """Return a coverage score and human-readable message for an account.
    Score 0-100. Based on field count and known-good path coverage.
    """
    db = get_db()
    # Count known paths for this site and how many have high quality scores
    path_rows = db.execute(
        "SELECT path, quality_score FROM site_paths WHERE site=? ORDER BY quality_score DESC",
        (source,)
    ).fetchall()
    total_paths    = len(path_rows)
    high_q_paths   = sum(1 for r in path_rows if r["quality_score"] >= 3.0)

    # Score from fields found (0 fields = 0, 5+ fields = 60 base points)
    field_score = min(field_count * 12, 60)
    # Score from path coverage (up to 40 points)
    path_score  = min(int((high_q_paths / max(total_paths, 1)) * 40), 40) if total_paths > 0 else 0
    score = field_score + path_score

    if score == 0:
        message = "No data found yet"
    elif field_count == 0:
        message = "Pages visited, but no fields extracted"
    elif score < 40:
        message = "Basic info found"
    elif score < 70:
        message = "Good coverage"
    else:
        message = "Full coverage"

    # Hint about missing high-value paths
    schema = _get_category_schema(source)
    hint = ""
    if schema and field_count < 3:
        if "certificate" in schema.get("priority_fields", []):
            hint = "Certificates or awards page may not have been visited yet"
        elif "credit" in schema.get("priority_fields", []):
            hint = "Benefits or credits page may not have been visited yet"

    return {"score": score, "message": message, "hint": hint, "field_count": field_count, "paths_visited": total_paths}


@app.route("/dashboard")
@require_login
def dashboard():
    expire_pending()
    db    = get_db()
    user  = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not user:
        session.clear()
        return redirect("/login")
    uid   = user["id"]
    acts  = db.execute(
        "SELECT * FROM actions WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
        (session["user_id"],),
    ).fetchall()
    url        = base_url()
    prompt     = build_prompt(user["api_key"], url)
    mcp_config = build_mcp_config(user["api_key"], url)
    feed       = build_feed_html(acts, url)
    topic      = ntfy_topic(user["api_key"])
    pending_count   = db.execute(
        "SELECT COUNT(*) FROM actions WHERE user_id=? AND status='pending'",
        (session["user_id"],),
    ).fetchone()[0]
    pending_display = "flex" if pending_count > 0 else "none"
    is_connected    = len(acts) > 0
    debug_mode      = request.args.get("debug") == "1"

    onboarding_banner = ""
    # Onboarding banner takes priority — only show the active (non-empty) state
    if not user["onboarded"] and len(acts) > 0:
        onboarding_banner = (
            '<div style="background:rgba(129,140,248,0.08);border:1px solid rgba(129,140,248,0.2);'
            'border-radius:10px;padding:14px 18px;display:flex;align-items:center;'
            'justify-content:space-between;gap:16px;margin:0 16px 8px">'
            '<div style="font-size:13px;color:#4338ca">'
            'Finish setting up Mighty to connect your first agent.</div>'
            '<a href="/onboarding" style="font-size:13px;font-weight:600;color:#6366f1;white-space:nowrap">'
            'Complete setup &#8594;</a></div>'
        )
    elif len(acts) < 4:
        # Email scan nudge: shown when user has few accounts and hasn't scanned yet
        _email_scan_done = db.execute(
            "SELECT COUNT(*) FROM email_suggestions WHERE user_id=?", (uid,)
        ).fetchone()[0]
        if not _email_scan_done:
            onboarding_banner = (
                '<div style="background:rgba(59,130,246,0.06);border:1px solid rgba(59,130,246,0.18);'
                'border-radius:10px;padding:14px 18px;display:flex;align-items:center;'
                'justify-content:space-between;gap:16px;margin:0 16px 8px">'
                '<div style="font-size:13px;color:#1d4ed8">💡 <strong>Find more accounts to track</strong> — '
                'scan your email to discover loyalty programs and subscriptions you already have.</div>'
                '<a href="/email-scan" style="font-size:13px;font-weight:600;color:#3b82f6;white-space:nowrap">'
                'Scan email &#8594;</a></div>'
            )

    welcome_state = ''
    if len(acts) == 0:
        if user["onboarded"]:
            # Onboarded but no activity yet — show feed area
            agent_status_indicator = (
                '<div style="display:flex;align-items:center;gap:6px;font-size:12px;color:#34d399">'
                '<div style="width:7px;height:7px;border-radius:50%;background:#34d399;flex-shrink:0"></div>'
                'Ready</div>'
            )
            agent_cta_button = (
                '<a href="/onboarding" style="display:inline-flex;align-items:center;gap:5px;padding:7px 14px;'
                'border-radius:8px;background:#ffffff;color:#6b7280;border:1px solid #e8e4de;font-size:12px;font-weight:600;'
                'text-decoration:none;white-space:nowrap;height:32px;box-sizing:border-box">+ Connect agent</a>'
            )
            feed = (
                '<div style="display:flex;flex-direction:column;align-items:center;'
                'justify-content:center;padding:60px 24px;text-align:center">'
                '<div style="font-size:14px;font-weight:600;color:#6b7280;margin-bottom:8px">No requests yet</div>'
                '<div style="font-size:13px;color:#9ca3af;line-height:1.6;max-width:280px">'
                'Ask your agent to do something that needs approval and the request will appear here.</div>'
                '</div>'
            )
            feed_col_hidden = ''
        else:
            # Not yet onboarded — show welcome state full-width, hide the feed column
            agent_status_indicator = ''
            agent_cta_button = ''
            feed_col_hidden = 'style="display:none"'
            welcome_state = (
                '<div style="display:flex;flex-direction:column;align-items:center;'
                'justify-content:center;padding:60px 24px;min-height:60vh">'
                '<div style="width:100%;max-width:360px;text-align:center">'
                '<div style="width:52px;height:52px;background:rgba(129,140,248,0.1);border:1px solid rgba(129,140,248,0.2);border-radius:14px;'
                'display:flex;align-items:center;justify-content:center;margin:0 auto 20px">'
                '<svg width="22" height="22" fill="none" stroke="#6366f1" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                '<polygon points="13,2 3,14 12,14 11,22 21,10 12,10"/></svg></div>'
                '<div style="font-size:22px;font-weight:700;color:#1c1917;margin-bottom:10px">'
                'Welcome to Mighty</div>'
                '<div style="font-size:14px;color:#6b7280;line-height:1.6;margin-bottom:28px">'
                'Connect your agent in about 5 minutes. Once connected, approval requests from your agent will appear here.</div>'
                '<a href="/onboarding" style="display:block;padding:13px 20px;'
                'background:#6366f1;color:#fff;border-radius:8px;font-size:14px;font-weight:600;'
                'text-decoration:none;margin-bottom:16px">Get started &#8594;</a>'
                '</div>'
                '</div>'
            )
    else:
        # Active state
        if is_connected:
            agent_status_indicator = (
                '<div style="display:flex;align-items:center;gap:6px;font-size:12px;color:#34d399"'
                ' title="Mighty agent is connected and active">'
                '<div style="width:7px;height:7px;border-radius:50%;background:#34d399;flex-shrink:0"></div>'
                'Active</div>'
            )
            agent_cta_button = (
                '<a href="/onboarding" style="display:inline-flex;align-items:center;gap:5px;padding:7px 14px;'
                'border-radius:8px;background:#ffffff;color:#6b7280;border:1px solid #e8e4de;font-size:12px;font-weight:600;'
                'text-decoration:none;white-space:nowrap;height:32px;box-sizing:border-box">+ Connect agent</a>'
            )
        else:
            agent_status_indicator = (
                '<div style="display:flex;align-items:center;gap:6px;font-size:12px;color:#9ca3af">'
                '<div style="width:7px;height:7px;border-radius:50%;background:#9ca3af;flex-shrink:0"></div>'
                'No agent</div>'
            )
            agent_cta_button = (
                '<a href="/onboarding" style="display:inline-flex;align-items:center;gap:5px;padding:7px 14px;'
                'border-radius:8px;background:#6366f1;color:#fff;font-size:12px;font-weight:600;'
                'text-decoration:none;white-space:nowrap">Set up agent &#8594;</a>'
            )
        feed_col_hidden = ''

    # ── Account data tab ──────────────────────────────────────────────────────
    # _fmt_sync is defined at module level (line ~762) — no local redefinition needed

    import re as _re
    from datetime import date as _date, datetime as _datetime
    # Label keywords that suggest an expiry/deadline — only matched against the LABEL, not the value,
    # to avoid false positives like "No Amount Due at this time" triggering on "due" in value text.
    # "promo" removed (too broad — matches "Promotion Status") — only "promo end/expir" would be valid.
    # "due" removed — too broad; covered by _URGENT_LABELS below.
    _TIME_LABELS = ("expir", "ends", "end date", "valid until", "offer end", "deadline", "renewal", "renew", "cancel")
    _URGENT_LABELS = ("payment due", "bill due", "past due", "amount due", "minimum payment due")
    # Labels that contain a date but should NEVER trigger an alert (they're informational, not action items)
    _SUPPRESS_ALERT_LABELS = ("autopay scheduled", "auto pay scheduled", "autopay date", "last payment", "member since", "account opened")
    _MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"

    def _parse_date_from_value(value: str):
        """Try to extract a date from a field value string. Returns a date object or None."""
        # MM/DD/YYYY or M/D/YYYY
        m = _re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b', value)
        if m:
            try: return _date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
            except ValueError: pass
        # YYYY-MM-DD
        m = _re.search(r'\b(\d{4})-(\d{2})-(\d{2})\b', value)
        if m:
            try: return _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except ValueError: pass
        # "Month DD, YYYY" or "Month DD YYYY"
        m = _re.search(rf'\b({_MONTHS})\w*\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b', value, _re.I)
        if m:
            try: return _datetime.strptime(f"{m.group(1)[:3].capitalize()} {m.group(2)} {m.group(3)}", "%b %d %Y").date()
            except ValueError: pass
        # "Month DD" without year — assume current or next year
        m = _re.search(rf'\b({_MONTHS})\w*\.?\s+(\d{{1,2}})\b', value, _re.I)
        if m:
            try:
                today = _date.today()
                d = _datetime.strptime(f"{m.group(1)[:3].capitalize()} {m.group(2)} {today.year}", "%b %d %Y").date()
                if d < today:
                    d = d.replace(year=today.year + 1)
                return d
            except ValueError: pass
        return None

    def _fmt_date_value(value: str) -> str:
        """Normalize a date value to human-readable 'Mon DD, YYYY' format if parseable."""
        d = _parse_date_from_value(value)
        if d:
            return d.strftime("%b %-d, %Y")
        return value

    def _classify_alert(label: str, value: str):
        """Return 'red', 'amber', or None.
        Date-aware: only flags if the date is within 60 days (amber) or past/≤7 days (red).
        Falls back to label-only heuristics when no date is parseable.
        Never alerts on informational date fields (autopay schedule, last payment, etc.)."""
        lbl_low = label.lower()

        # Suppress alerts entirely for informational labels — dates here are FYI, not action items
        if any(s in lbl_low for s in _SUPPRESS_ALERT_LABELS):
            return None

        today = _date.today()
        parsed = _parse_date_from_value(value)

        if parsed is not None:
            delta = (parsed - today).days
            if delta < -7:   return None      # more than a week past — stale, no alert
            if delta <= 7:   return "red"     # past-due or imminent
            if delta <= 60:  return "amber"   # within 2 months
            return None                        # too far out — no alert

        # No parseable date — fall back to label-only keywords (NOT value text, to avoid false positives
        # like "No Amount Due at this time" triggering "due" match from value)
        if any(t in lbl_low for t in _URGENT_LABELS):
            return "red"
        if any(t in lbl_low for t in _TIME_LABELS):
            return "amber"
        return None

    # Promote any pending candidates (confidence>=0.60) that weren't auto-enabled before
    # the threshold was lowered. Idempotent — marks promoted rows 'auto_approved'.
    try:
        _promote_pending_candidates(uid)
    except Exception:
        pass

    # Step 1: get ALL connected accounts (have credentials, not internal keys)
    cred_rows = get_db().execute(
        "SELECT source, extra_enc FROM account_credentials WHERE user_id=?",
        (user["id"],)
    ).fetchall()
    connected_sources = {r["source"] for r in cred_rows if not r["source"].startswith("_")}

    # Step 2: load discovered fields and synced data per source
    discovered_by_source: dict = {}
    for cr in cred_rows:
        if cr["extra_enc"]:
            try:
                ex = json.loads(decrypt_cred(user["id"], cr["extra_enc"]))
                discovered = ex.get("discovered_fields", [])
                enabled    = set(ex.get("enabled_fields", []))
                review_req = set(ex.get("review_required_fields", []))
                if discovered and enabled:
                    discovered_by_source[cr["source"]] = {
                        "fields": discovered,
                        "enabled": enabled,
                        "review_required": review_req,
                    }
                elif ex.get("discovery_failed"):
                    discovered_by_source[cr["source"]] = {
                        "fields": [], "enabled": set(),
                        "review_required": set(), "failed": True,
                    }
            except Exception:
                pass

    acct_rows = get_db().execute(
        "SELECT * FROM account_data WHERE user_id=? ORDER BY synced_at DESC",
        (user["id"],)
    ).fetchall()
    # Convert to plain dicts so .get() works safely even when columns are
    # missing from older production DB schemas (e.g. sync_status, sync_failure_reason).
    synced_map = {r["source"]: dict(r) for r in acct_rows}

    # Global "last synced" — most recent sync across all accounts
    _all_synced_ats = [v["synced_at"] for v in synced_map.values() if v.get("synced_at")]
    _global_last_synced = max(_all_synced_ats) if _all_synced_ats else None
    if _global_last_synced:
        _glbl, _glbc, _glbi = _freshness_label(_global_last_synced)
        _global_sync_label = f'Synced {_glbl}'
    else:
        _global_sync_label = ""

    # Step 3: build cards for ALL connected accounts, grouped by category
    configured = connected_sources

    # Group configured sources by category
    _cat_order = []
    _cat_map = {}
    for key, name, icon, color, cat in SUPPORTED_SITES:
        if key not in configured:
            continue
        if cat not in _cat_map:
            _cat_order.append(cat)
            _cat_map[cat] = []
        _cat_map[cat].append((key, name, icon, color))

    # Also render custom (user-captured) accounts
    _CUSTOM_COLORS = ["#f0f9ff","#f0fdf4","#fdf4ff","#fffbeb","#fef2f2","#f0fdfa"]
    _ci = 0
    for cr in cred_rows:
        src = cr["source"]
        if not src.startswith("custom_"):
            continue
        try:
            ex = json.loads(decrypt_cred(user["id"], cr["extra_enc"] or "") or "{}")
        except Exception:
            ex = {}
        c_name  = ex.get("display_name") or src.replace("custom_", "").replace("_", " ").title()
        c_cat   = ex.get("category") or "Other"
        c_icon  = ex.get("icon") or "📋"
        c_color = ex.get("color") or _CUSTOM_COLORS[_ci % len(_CUSTOM_COLORS)]
        _ci += 1
        if c_cat not in _cat_map:
            _cat_order.append(c_cat)
            _cat_map[c_cat] = []
        _cat_map[c_cat].append((src, c_name, c_icon, c_color))

    cards_html = ""
    total_expiring = 0
    login_required_accounts = []

    for cat in _cat_order:
        grid_cards = ""
        for src, display_name, icon, color in _cat_map[cat]:
            row   = synced_map.get(src)
            data  = decrypt_account_data(user["id"], row["data_enc"] or "") if row else {}

            # Determine items to display
            review_required_keys: set = set()
            if src in discovered_by_source:
                disc  = discovered_by_source[src]
                review_required_keys = disc.get("review_required", set())
                items = [
                    {
                        "key": f["key"],
                        "label": f["label"],
                        "value": f.get("value", "–"),
                        "source_snippet": f.get("source_snippet", ""),
                        "_type": classify_benefit(f["label"], str(f.get("value", "")), src),
                    }
                    for f in _post_filter_fields(disc["fields"], source=src)
                    if f.get("key") in disc["enabled"]
                ]
            else:
                items = data.get("items", [])
                # Reclassify items that are missing _type (older stored data)
                for _ci in items:
                    if not _ci.get("_type") or _ci.get("_type") == "other":
                        _ci["_type"] = classify_benefit(_ci.get("label",""), str(_ci.get("value","")), src)

            synced_at   = row["synced_at"] if row else ""
            sync_status = data.get("sync_status", "ok") if row else ""

            # Batch-fetch field_observations for the Why? modal (one query per card)
            obs_map: dict = {}
            try:
                _obs_rows = get_db().execute(
                    "SELECT field_key, first_seen, last_seen, seen_count "
                    "FROM field_observations WHERE user_id=? AND source=?",
                    (uid, src)
                ).fetchall()
                obs_map = {r["field_key"]: r for r in _obs_rows}
            except Exception:
                pass

            cand_count = 0
            try:
                cand_count = get_db().execute(
                    "SELECT COUNT(*) FROM field_candidates WHERE user_id=? AND source=? AND status='pending'",
                    (uid, src)
                ).fetchone()[0] or 0
            except Exception:
                pass
            if cand_count > 0:
                plural = "s" if cand_count > 1 else ""
                _cand_url = f"/candidates/{src}"
                cand_notice = (
                    f'<div style="margin:8px 0 4px;padding:6px 10px;background:#eff6ff;'
                    f'border-radius:6px;font-size:12px;color:#1d4ed8;cursor:pointer" '
                    f'onclick="window.location=&apos;{_cand_url}&apos;">'
                    f'&#10024; Mighty found {cand_count} possible new benefit{plural} &#8594; Review</div>'
                )
            else:
                cand_notice = ""
            status_color = "#30d158"

            # For utility/telecom sources, promote billing fields to the front
            _UTILITY_SOURCES_CARD = {
                "xfinity", "comcast", "spectrum", "cox", "centurylink", "att_internet",
                "pge", "sdge", "palo_alto_utilities", "verizon", "tmobile",
            }
            _UTILITY_HERO_KEYS = {
                "amount_due", "balance_due", "current_balance", "next_payment",
                "monthly_rate", "bill_amount", "account_balance", "auto_pay", "due_date",
            }
            if src in _UTILITY_SOURCES_CARD:
                preferred = [it for it in items if it.get("key") in _UTILITY_HERO_KEYS]
                rest = [it for it in items if it.get("key") not in _UTILITY_HERO_KEYS]
                items = preferred + rest
            else:
                # Sort by benefit type: certs/credits first (action-driving), points/miles last (inventory)
                def _card_type_priority(item):
                    t = item.get("_type", "other")
                    if t == "certificate":                   return 0
                    if t in ("travel_credit", "cash_credit"): return 1
                    if t == "elite_status":                  return 2
                    if t == "upcoming_event":                return 3
                    if t in ("partner_benefit", "membership"): return 4
                    if t in ("payment_due", "renewal"):      return 5
                    if t == "other":                         return 6
                    if t == "points_balance":                return 7
                    if t == "progress_toward":               return 8
                    return 6
                items = sorted(items, key=_card_type_priority)

            # Visual weight: compact class for accounts with no high-value items
            # (no certs, credits, or elite status — just utility data like bills/balances)
            _has_highval = any(
                it.get("_type") in ("certificate","travel_credit","cash_credit","elite_status")
                for it in items
            )
            _compact_cls = "" if _has_highval else " is-compact"

            # Status/level — show tier as a colored chip directly below the account name
            _status_item = next((it for it in items if it.get("_type") == "elite_status"), None)
            _status_inline_html = ""
            if _status_item and str(_status_item.get("value","")).strip():
                _sv = str(_status_item["value"]).strip()
                _sv_lc = _sv.lower()
                # Color by tier level
                if any(k in _sv_lc for k in ["diamond","globalist","1k","executive platinum","titanium","chairman","ambassador"]):
                    _st_color, _st_bg = "#92400e", "#fef3c7"   # amber — top
                elif any(k in _sv_lc for k in ["platinum","gold","sapphire","silver","premier","rouge"]):
                    _st_color, _st_bg = "#5b21b6", "#ede9fe"   # purple — mid-high
                else:
                    _st_color, _st_bg = "#1e40af", "#dbeafe"   # blue — base
                _status_inline_html = (
                    f'<div style="display:inline-block;margin-top:3px;font-size:11px;font-weight:600;'
                    f'color:{_st_color};background:{_st_bg};border-radius:8px;'
                    f'padding:2px 8px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%">'
                    f'◆ {he(_sv)}</div>'
                )

            # Benefit badges — count certs and credits for card header
            _badge_certs   = sum(1 for it in items if it.get("_type") == "certificate")
            _badge_credits = sum(1 for it in items if it.get("_type") in ("travel_credit","cash_credit"))
            _badges_html = ""
            if _badge_certs:
                _badges_html += (
                    f'<span style="font-size:10px;font-weight:600;color:#1d4ed8;background:#dbeafe;'
                    f'border-radius:10px;padding:2px 7px;white-space:nowrap">🎫 {_badge_certs}</span>'
                )
            if _badge_credits:
                _badges_html += (
                    f'<span style="font-size:10px;font-weight:600;color:#047857;background:#d1fae5;'
                    f'border-radius:10px;padding:2px 7px;white-space:nowrap">💳 {_badge_credits}</span>'
                )
            if _badges_html:
                _badges_html = (
                    f'<div style="display:flex;gap:4px;flex-wrap:wrap;margin-top:3px">{_badges_html}</div>'
                )

            # Separate hero stat from secondary stats, and find alerts
            hero_item = None
            secondary_items = []
            alert_item = None
            alert_level = None

            # Check if autopay is enrolled — suppresses payment due date alerts
            _AUTOPAY_LABELS = ("auto pay", "autopay", "automatic payment", "auto-pay")
            _AUTOPAY_ENROLLED = ("enrolled", "active", "on", "yes", "enabled", "scheduled")
            _autopay_on = any(
                any(ap in i.get("label", "").lower() for ap in _AUTOPAY_LABELS)
                and any(ev in i.get("value", "").lower() for ev in _AUTOPAY_ENROLLED)
                for i in items
            )
            _PAYMENT_DUE_LABELS = ("payment due", "due date", "bill due", "amount due", "minimum payment", "past due")

            for i in items:
                lbl = i.get("label", "")
                val = i.get("value", "")
                # Suppress payment due date alerts when autopay is confirmed enrolled
                # (the amount still shows, just not as an amber/red alert)
                if _autopay_on and any(p in lbl.lower() for p in _PAYMENT_DUE_LABELS):
                    if not hero_item:
                        hero_item = i
                    else:
                        secondary_items.append(i)
                    continue
                lvl = _classify_alert(lbl, val)
                if lvl and not alert_item:
                    alert_item = i
                    alert_level = lvl
                    total_expiring += 1
                elif not hero_item:
                    hero_item = i
                else:
                    secondary_items.append(i)

            # If we have an alert but no hero, use first secondary as hero
            if alert_item and not hero_item and secondary_items:
                hero_item = secondary_items.pop(0)
            # Last-resort: if still no hero but items exist, force first item
            # (prevents "Discovering fields…" spinner on synced cards)
            if not hero_item and items:
                hero_item = items[0]
                secondary_items = [i for i in items if i is not hero_item]

            # Build card hero section
            if hero_item:
                card_hero_html = (
                    f'<div class="acct-divider"></div>'
                    f'<div class="acct-hero">'
                    f'<div class="hero-val" title="{he(hero_item["value"])}">{he(hero_item["value"])}</div>'
                    f'<div class="hero-lbl">{he(hero_item["label"])}</div>'
                    f'</div>'
                )
            elif sync_status == "needs_first_visit":
                _nfv_login_url = SOURCE_CAPABILITIES.get(src, {}).get("login_url", "")
                _nfv_link = (
                    f'<a href="{he(_nfv_login_url)}" target="_blank" rel="noopener" '
                    f'style="display:inline-block;margin-top:8px;padding:5px 12px;background:#6366f1;'
                    f'color:#fff;border-radius:6px;font-size:11px;font-weight:600;text-decoration:none">'
                    f'Open {he(display_name)} →</a>'
                ) if _nfv_login_url else ""
                card_hero_html = (
                    f'<div class="acct-divider"></div>'
                    f'<div class="acct-hero">'
                    f'<div style="color:#6b7280;font-size:12px;line-height:1.5">'
                    f'Visit your {he(display_name)} account page to start syncing. '
                    f'The extension will capture it automatically.'
                    f'</div>'
                    f'{_nfv_link}'
                    f'</div>'
                )
                status_color = "#a78bfa"
            elif sync_status == "login_required":
                card_hero_html = (
                    f'<div class="acct-divider"></div>'
                    f'<div class="acct-hero">'
                    f'<div style="color:#ef4444;font-size:12px;font-weight:500">⚠ You\'ve been logged out of {he(display_name)}</div>'
                    f'<div style="color:#9ca3af;font-size:11px;margin-top:3px">Log back in via Chrome, then click the sync button ↻</div>'
                    f'</div>'
                )
                status_color = "#ef4444"
                login_required_accounts.append(display_name)
            elif synced_at:
                # Show "No account data" only when discovery explicitly failed or
                # sync returned no_data — never based on age alone (age-based
                # check was too aggressive and hid valid cards after 5 min).
                _disc_info = discovered_by_source.get(src, {})
                _no_data = _disc_info.get("failed", False) or sync_status == "no_data"
                if _no_data:
                    _fail_key   = (data.get("sync_failure_reason") or "") if row else ""
                    _fail_label, _fail_action = SYNC_FAILURE_MESSAGES.get(_fail_key, ("No data found", "Visit your account overview page in Chrome, then click sync."))
                    card_hero_html = (
                        f'<div class="acct-divider"></div>'
                        f'<div class="acct-hero">'
                        f'<div style="color:#d97706;font-size:12px;font-weight:500">⚠ {he(_fail_label)}</div>'
                        f'<div style="color:#9ca3af;font-size:11px;margin-top:3px">{he(_fail_action)}</div>'
                        f'</div>'
                    )
                    status_color = "#f59e0b"
                else:
                    card_hero_html = (
                        f'<div class="acct-divider"></div>'
                        f'<div class="acct-hero" data-discovering="1">'
                        f'<div style="color:#6366f1;font-size:12px;font-weight:500">'
                        f'<span style="display:inline-block;animation:spin 1.2s linear infinite;margin-right:4px">↻</span>'
                        f'Discovering fields…</div>'
                        f'</div>'
                    )
                    status_color = "#9ca3af"
            else:
                card_hero_html = (
                    f'<div class="acct-divider"></div>'
                    f'<div class="acct-hero">'
                    f'<div style="color:#c0bab4;font-style:italic;font-size:12px">Awaiting sync…</div>'
                    f'</div>'
                )
                status_color = "#9ca3af"

            # Filter noise items (bare IDs / member numbers) from secondary visible slots
            # so real benefits/balances don't get pushed behind "more fields"
            _NOISE_KEYS = {
                "skymiles_number","member_number","member_id","account_number",
                "loyalty_number","ff_number","membership_number","rewards_number",
                "member_since","card_number","policy_number",
            }
            _NOISE_LABELS = ("member since","member id","member number","account number",
                             "loyalty id","loyalty number","ff number","membership number",
                             "skymiles number","mileage plus number","rapid rewards number",
                             "card number","policy number","honors number")
            def _is_noise(it):
                k = (it.get("key") or "").lower()
                l = (it.get("label") or "").lower()
                return k in _NOISE_KEYS or any(nl in l for nl in _NOISE_LABELS)

            # Partition secondary into signal (show first) vs noise (show last)
            _sec_signal = [i for i in secondary_items if not _is_noise(i)]
            _sec_noise   = [i for i in secondary_items if _is_noise(i)]
            secondary_items = _sec_signal + _sec_noise

            # Build secondary stats (up to 3 visible)
            sec_html = ""
            if secondary_items:
                sec_row_parts = []
                for i in secondary_items[:3]:
                    review_badge = (
                        '<span style="font-size:10px;color:#f59e0b;margin-left:4px;cursor:help" '
                        'title="Lower confidence — may need verification">⚠ review</span>'
                        if i.get("key") in review_required_keys else ""
                    )
                    snip = i.get("source_snippet", "") or i.get("source_url", "") or i.get("url", "")
                    _fkey = i.get("key", "")
                    _fconf = i.get("confidence", None)
                    _obs = obs_map.get(_fkey)
                    _first_seen_str = _fmt_sync(_obs["first_seen"]) if _obs and _obs["first_seen"] else None
                    _last_seen_str  = _fmt_sync(_obs["last_seen"])  if _obs and _obs["last_seen"]  else (_fmt_sync(synced_at) if synced_at else None)
                    _src_url = i.get("source_url") or i.get("url") or (src.replace("_", ".") if src else None)
                    _conf_label = _confidence_label(float(_fconf)) if _fconf is not None else None
                    _why_detail_rows = []
                    if _conf_label:
                        _why_detail_rows.append(f"<strong>Confidence:</strong> {he(_conf_label)}")
                    if _first_seen_str:
                        _why_detail_rows.append(f"<strong>Discovered:</strong> {he(_first_seen_str)}")
                    if _last_seen_str:
                        _why_detail_rows.append(f"<strong>Last verified:</strong> {he(_last_seen_str)}")
                    if _src_url:
                        _why_detail_rows.append(f"<strong>Source:</strong> {he(_src_url[:80])}")
                    _why_detail_html = "<br>".join(_why_detail_rows)
                    _why_snippet_html = (
                        f'<div style="font-size:11px;color:#6b7280;margin-top:4px;font-style:italic">{he(snip[:200])}</div>'
                        if snip else ""
                    )
                    why_html = (
                        f'<span class="why-link" onclick="toggleSnippet(this)" '
                        f'style="font-size:10px;color:#60a5fa;cursor:pointer;margin-left:6px;user-select:none" '
                        f'title="Show discovery details">Why?</span>'
                        f'<div class="snippet-reveal" style="display:none;font-size:11px;color:#374151;'
                        f'background:#f9fafb;border:1px solid #e5e7eb;border-radius:4px;padding:6px 8px;'
                        f'margin-top:4px;line-height:1.6">'
                        f'{_why_detail_html}{_why_snippet_html}</div>'
                    ) if (debug_mode and (_why_detail_rows or snip)) else ""
                    sec_row_parts.append(
                        f'<div class="sec-row">'
                        f'<span class="sec-lbl">{he(i["label"])}{review_badge}</span>'
                        f'<span class="sec-val" title="{he(i["value"])}">{he(i["value"])}{why_html}</span>'
                        f'</div>'
                    )
                sec_html = f'<div class="acct-secondary">{"".join(sec_row_parts)}</div>'

            # Build expanded fields (all items not already shown)
            shown_keys = set()
            if hero_item:
                shown_keys.add(hero_item.get("key"))
            if alert_item:
                shown_keys.add(alert_item.get("key"))
            for i in secondary_items[:3]:
                shown_keys.add(i.get("key"))
            extra_items = [i for i in items if i.get("key") not in shown_keys]
            expanded_html = ""
            if extra_items:
                exp_row_parts = []
                for i in extra_items:
                    review_badge = (
                        '<span style="font-size:10px;color:#f59e0b;margin-left:4px;cursor:help" '
                        'title="Lower confidence — may need verification">⚠ review</span>'
                        if i.get("key") in review_required_keys else ""
                    )
                    snip = i.get("source_snippet", "") or i.get("source_url", "") or i.get("url", "")
                    _fkey = i.get("key", "")
                    _fconf = i.get("confidence", None)
                    _obs = obs_map.get(_fkey)
                    _first_seen_str = _fmt_sync(_obs["first_seen"]) if _obs and _obs["first_seen"] else None
                    _last_seen_str  = _fmt_sync(_obs["last_seen"])  if _obs and _obs["last_seen"]  else (_fmt_sync(synced_at) if synced_at else None)
                    _src_url = i.get("source_url") or i.get("url") or (src.replace("_", ".") if src else None)
                    _conf_label = _confidence_label(float(_fconf)) if _fconf is not None else None
                    _why_detail_rows = []
                    if _conf_label:
                        _why_detail_rows.append(f"<strong>Confidence:</strong> {he(_conf_label)}")
                    if _first_seen_str:
                        _why_detail_rows.append(f"<strong>Discovered:</strong> {he(_first_seen_str)}")
                    if _last_seen_str:
                        _why_detail_rows.append(f"<strong>Last verified:</strong> {he(_last_seen_str)}")
                    if _src_url:
                        _why_detail_rows.append(f"<strong>Source:</strong> {he(_src_url[:80])}")
                    _why_detail_html = "<br>".join(_why_detail_rows)
                    _why_snippet_html = (
                        f'<div style="font-size:11px;color:#6b7280;margin-top:4px;font-style:italic">{he(snip[:200])}</div>'
                        if snip else ""
                    )
                    why_html = (
                        f'<span class="why-link" onclick="toggleSnippet(this)" '
                        f'style="font-size:10px;color:#60a5fa;cursor:pointer;margin-left:6px;user-select:none" '
                        f'title="Show discovery details">Why?</span>'
                        f'<div class="snippet-reveal" style="display:none;font-size:11px;color:#374151;'
                        f'background:#f9fafb;border:1px solid #e5e7eb;border-radius:4px;padding:6px 8px;'
                        f'margin-top:4px;line-height:1.6">'
                        f'{_why_detail_html}{_why_snippet_html}</div>'
                    ) if (debug_mode and (_why_detail_rows or snip)) else ""
                    exp_row_parts.append(
                        f'<div class="exp-row">'
                        f'<span class="exp-lbl" title="{he(i["label"])}">{he(i["label"])}{review_badge}</span>'
                        f'<span class="exp-val" title="{he(i["value"])}">{he(i["value"])}{why_html}</span>'
                        f'</div>'
                    )
                # Add edit button at bottom of expanded section
                exp_row_parts.append(
                    f'<div style="padding-top:8px;margin-top:4px;border-top:1px solid #f3f4f6">'
                    f'<button class="acct-edit-btn" onclick="openDashFieldModal(\'{he(src)}\',\'{he(display_name)}\')">Edit fields</button>'
                    f'</div>'
                )
                expanded_html = f'<div class="acct-expanded">{"".join(exp_row_parts)}</div>'

            # Build alert row
            alert_html = ""
            if alert_item:
                cls = "acct-alert-red" if alert_level == "red" else "acct-alert-amber"
                alert_html = (
                    f'<div class="acct-alert {cls}">'
                    f'<div>'
                    f'<div class="alert-lbl">{he(alert_item["label"])}</div>'
                    f'<div class="alert-sub">{he(_fmt_date_value(alert_item["value"]))}</div>'
                    f'</div>'
                    f'</div>'
                )

            # Detect login-wall values
            _BAD = ("log in", "sign in", "login to", "no match found")
            bad_fields = src in discovered_by_source and any(
                any(b in str(f.get("value","")).lower() for b in _BAD)
                for f in discovered_by_source[src]["fields"]
            )
            bad_banner = (
                f'<div style="margin:0 10px 8px;background:#fffbef;border:0.5px solid rgba(245,158,11,0.35);border-radius:7px;'
                f'padding:7px 10px;display:flex;align-items:center;gap:7px;font-size:11px">'
                f'<span>⚠️</span>'
                f'<span style="flex:1;color:#b45309">Couldn\'t log in — fields may be stale.</span>'
                f'<a href="#" onclick="resetFields(\'{he(src)}\');return false;" style="color:#6366f1;font-weight:600;text-decoration:none;white-space:nowrap">Reset →</a>'
                f'</div>'
            ) if bad_fields else ""

            sync_label = f'Synced {_fmt_sync(synced_at)}' if synced_at else 'Not yet synced'
            stale_cls = " is-stale" if not synced_at else ""
            expiring_cls = " is-expiring" if alert_item else ""
            _flabel, _fcolor, _ficon = _freshness_label(synced_at, sync_status)
            _fw = "700" if _fcolor == "#dc2626" else "500"
            _fprefix = f"{_ficon} " if _ficon else ""
            # Promote status_color to amber/red when data is stale, even if sync_status="ok".
            # This ensures the dot reflects actual freshness, not just last-known sync state.
            if status_color == "#30d158" and _fcolor in ("#f59e0b", "#dc2626"):
                status_color = _fcolor
            synced_title = (
                f"Synced {_fmt_sync(synced_at)}" if synced_at else "Not yet synced"
            )
            # Show freshness text whenever data is amber/red or missing
            _is_sync_problem = _fcolor in ("#f59e0b", "#dc2626") or not synced_at
            freshness_html = (
                f'<span style="font-size:11px;color:{_fcolor};font-weight:{_fw}">{_fprefix}{_flabel}</span>'
                if _is_sync_problem else ""
            )

            # Consistent footer for all cards
            _BAD_VALUES = {"", "—", "–", "—", "no data", "none", "n/a", "-"}
            _no_data = (not items or
                        all(str(i.get("value","")).strip().lower() in _BAD_VALUES for i in items))
            if extra_items:
                expand_btn = (
                    f'<button class="acct-expand-btn" onclick="toggleExpand(this)" '
                    f'data-count="{len(extra_items)}">'
                    f'<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
                    f'{len(extra_items)} more field{"s" if len(extra_items)!=1 else ""}'
                    f'</button>'
                )
            elif _no_data:
                # No data yet — don't show a confusing button
                expand_btn = (
                    f'<span style="font-size:11px;color:#c0bab4">No data synced yet</span>'
                )
            else:
                # Has data, all fields already shown — nothing extra to expand
                expand_btn = ""
            card_footer = (
                f'<div class="acct-footer">'
                f'{expand_btn}'
                f'</div>'
            )

            # Completeness score for card header + coverage gap hints
            cat_expected = _EXPECTED_FIELDS.get(_source_category(src), {})
            if cat_expected:
                gaps = _coverage_gaps(src, [it.get("key","") for it in items])
                found_count = len(cat_expected) - len(gaps)
                completeness_pct = int(found_count / len(cat_expected) * 100)
                completeness_color = "#22c55e" if completeness_pct >= 80 else "#f59e0b" if completeness_pct >= 50 else "#ef4444"
                completeness_badge = (
                    f'<span style="font-size:10px;color:{completeness_color};font-weight:600;'
                    f'background:{completeness_color}18;padding:1px 5px;border-radius:10px;margin-left:6px">'
                    f'{completeness_pct}%</span>'
                )
                # Show top 2 coverage gaps inline (always visible, not behind sync health expand)
                if gaps:
                    top_gaps = gaps[:2]
                    gap_hints = "".join(
                        f'<span style="font-size:10px;color:#d1d5db;margin-right:8px">? {he(gdesc)}</span>'
                        for _, gdesc in top_gaps
                    )
                    gaps_inline = f'<div style="margin:4px 0 2px">{gap_hints}</div>'
                else:
                    gaps_inline = ""

                # Build coverage detail block (server-rendered, shown when health panel expands)
                coverage_lines = []
                found_labels = {}
                for it in items:
                    fk = it.get("key", "")
                    for exp_key, exp_desc in cat_expected.items():
                        tokens_exp = [t for t in exp_key.split("_") if len(t) > 3]
                        tokens_fk  = [t for t in fk.split("_")  if len(t) > 3]
                        if exp_key == fk or (len(tokens_exp) >= 2 and len(set(tokens_exp) & set(tokens_fk)) >= 2) \
                           or (len(exp_key) > 7 and exp_key in fk):
                            found_labels[exp_key] = it.get("label", exp_desc)
                for exp_key, exp_desc in cat_expected.items():
                    if exp_key in found_labels:
                        coverage_lines.append(
                            f'<div style="font-size:11px;color:#16a34a;padding:1px 0">'
                            f'✓ {he(found_labels[exp_key])}</div>'
                        )
                    else:
                        coverage_lines.append(
                            f'<div style="font-size:11px;color:#d97706;padding:1px 0">'
                            f'? {he(exp_desc)}</div>'
                        )
                coverage_block = (
                    f'<div style="margin-bottom:8px;padding-bottom:6px;border-bottom:1px solid #f3f4f6">'
                    f'<div style="font-size:10px;font-weight:600;color:#9ca3af;text-transform:uppercase;'
                    f'letter-spacing:.05em;margin-bottom:4px">Coverage {completeness_pct}%</div>'
                    + "".join(coverage_lines) +
                    f'</div>'
                )
                # Coverage face — always visible on card face
                cov_color = "#16a34a" if completeness_pct >= 80 else "#d97706" if completeness_pct >= 50 else "#dc2626"

                # Build found/missing lists
                found_lines = []
                missing_lines = []
                for exp_key, exp_desc in cat_expected.items():
                    matched = any(
                        exp_key == it.get("key", "") or
                        (len([t for t in exp_key.split("_") if len(t) > 3]) >= 2 and
                         len(set([t for t in exp_key.split("_") if len(t) > 3]) &
                             set([t for t in it.get("key", "").split("_") if len(t) > 3])) >= 2) or
                        (len(exp_key) > 7 and exp_key in it.get("key", ""))
                        for it in items
                    )
                    if matched:
                        found_lines.append(f'<span style="color:#16a34a">✓ {he(exp_desc)}</span>')
                    else:
                        missing_lines.append(f'<span style="color:#9ca3af">• {he(exp_desc)}</span>')

                found_html = ""
                if found_lines:
                    found_html = (
                        '<div style="font-size:10px;line-height:1.7;margin-top:4px">'
                        + " &nbsp;".join(found_lines[:4]) +
                        '</div>'
                    )

                searching_html = ""
                if missing_lines:
                    searching_html = (
                        '<div style="font-size:10px;color:#9ca3af;margin-top:2px">'
                        f'Not yet found: ' + " &nbsp;".join(missing_lines[:3]) +
                        ('…' if len(missing_lines) > 3 else '') +
                        '</div>'
                    )

                coverage_face = (
                    f'<div style="margin-top:5px;padding-top:5px;border-top:1px solid #f3f4f6">'
                    f'<div style="display:flex;align-items:center;gap:4px">'
                    f'<div style="flex:1;height:2px;background:#e5e7eb;border-radius:2px">'
                    f'<div style="height:2px;width:{completeness_pct}%;background:{cov_color};border-radius:2px"></div>'
                    f'</div>'
                    f'<span style="font-size:10px;color:{cov_color};font-weight:600;flex-shrink:0">{completeness_pct}%</span>'
                    f'</div>'
                    f'</div>'
                )
            else:
                completeness_badge = ""
                gaps_inline = ""
                coverage_block = ""
                completeness_pct = None
                gaps = []
                coverage_face = ""

            # Simplified health footer — no "Sync health" label, just a subtle details expander
            health_footer = (
                f'<div class="card-health-footer" style="padding:4px 14px 8px">'
                + (
                    f'<button onclick="toggleHealth(this,\'{he(src)}\')" style="background:none;border:none;cursor:pointer;font-size:10px;color:#9ca3af;padding:0;display:flex;align-items:center;gap:2px;margin-top:2px">'
                    f'<span class="health-chevron" style="font-size:9px">&#9656;</span> details'
                    f'</button>'
                    f'<div class="health-detail" style="display:none;margin-top:6px;font-size:12px;color:#6b7280" data-source="{he(src)}">{coverage_block}</div>'
                    if coverage_block else ""
                )
                + f'</div>'
            )

            grid_cards += (
                f'<div class="acct-card{stale_cls}{expiring_cls}{_compact_cls}" data-name="{he(display_name)}" data-sync-status="{he(sync_status)}">'
                f'<div class="acct-card-header">'
                f'<div style="flex:1;min-width:0">'
                f'<div class="acct-name">{he(display_name)}{completeness_badge}</div>'
                f'{_status_inline_html}'
                + _badges_html
                + (f'<div class="acct-sync-time" data-synced="{he(synced_at)}">{freshness_html}</div>' if freshness_html else '')
                + f'<div class="acct-sync-time" data-synced="{he(synced_at)}" style="display:none"></div>'
                f'{coverage_face}'
                f'</div>'
                f'<div class="acct-controls">'
                f'<div style="width:7px;height:7px;border-radius:50%;background:{status_color};flex-shrink:0;cursor:help" title="{synced_title}"></div>'
                + (
                    f'<a href="{he(_card_url)}" target="_blank" rel="noopener" title="Open {he(display_name)}" class="acct-refresh-btn" style="text-decoration:none;font-size:11px">'
                    f'<svg width="11" height="11" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M7 1h4v4M11 1L5.5 6.5M5 2H2a1 1 0 0 0-1 1v7a1 1 0 0 0 1 1h7a1 1 0 0 0 1-1V8" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
                    f'</a>'
                    if (_card_url := (SITE_ENTRY_URL.get(src) or (row or {}).get("entry_url", ""))) else ""
                )
                + f'<button onclick="syncAccount(\'{he(src)}\', this)" title="Sync this account" class="acct-refresh-btn">↻</button>'
                f'</div>'
                f'</div>'
                f'{bad_banner}'
                f'{card_hero_html}'
                f'{sec_html}'
                f'{alert_html}'
                f'{cand_notice}'
                f'{expanded_html}'
                f'{health_footer}'
                f'{card_footer}'
                f'</div>'
            )

        if grid_cards:
            cards_html += (
                f'<div class="cat-group">'
                f'<div class="cat-header">'
                f'<span class="cat-label">{he(cat)}</span>'
                f'<div class="cat-rule"></div>'
                f'</div>'
                f'<div class="card-grid">{grid_cards}</div>'
                f'</div>'
            )

    account_data_html = cards_html if cards_html else (
        '<div style="text-align:center;padding:48px 24px;max-width:540px;margin:0 auto">'
        '<div style="font-size:36px;margin-bottom:14px">🔗</div>'
        '<div style="font-size:16px;font-weight:700;color:#1c1917;margin-bottom:8px">Connect your first account</div>'
        '<div style="font-size:13px;color:#6b7280;line-height:1.65;margin-bottom:28px">'
        'Mighty uses the Chrome extension to read your account data directly from the sites you\'re already logged into — '
        'no passwords stored here.</div>'
        '<div style="display:flex;flex-direction:column;gap:10px;text-align:left;margin-bottom:28px">'
        '<div style="display:flex;align-items:flex-start;gap:12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px">'
        '<div style="font-size:18px;flex-shrink:0">1️⃣</div>'
        '<div><div style="font-size:13px;font-weight:600;color:#111827">Install the Chrome extension</div>'
        '<div style="font-size:12px;color:#6b7280;margin-top:2px">Then visit <a href="/extension-setup" target="_blank" style="color:#6366f1;font-weight:500">Settings → Setup Extension</a> to auto-configure it</div></div></div>'
        '<div style="display:flex;align-items:flex-start;gap:12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px">'
        '<div style="font-size:18px;flex-shrink:0">2️⃣</div>'
        '<div><div style="font-size:13px;font-weight:600;color:#111827">Log into your accounts in Chrome</div>'
        '<div style="font-size:12px;color:#6b7280;margin-top:2px">The extension automatically detects and captures account pages as you browse</div></div></div>'
        '<div style="display:flex;align-items:flex-start;gap:12px;background:#f9fafb;border:1px solid #e5e7eb;border-radius:10px;padding:12px 14px">'
        '<div style="font-size:18px;flex-shrink:0">3️⃣</div>'
        '<div><div style="font-size:13px;font-weight:600;color:#111827">Your data appears here automatically</div>'
        '<div style="font-size:12px;color:#6b7280;margin-top:2px">Points, balances, alerts, and expiring benefits — all in one place</div></div></div>'
        '</div>'
        '<a href="/credentials" style="display:inline-block;padding:10px 22px;background:#6366f1;'
        'color:#fff;border-radius:8px;font-size:13px;font-weight:600;text-decoration:none;margin-right:10px">'
        '+ Connect account manually</a>'
        '<a href="/extension-setup" target="_blank" style="display:inline-block;padding:10px 22px;background:#fff;'
        'color:#6366f1;border:1px solid #c7d2fe;border-radius:8px;font-size:13px;font-weight:600;text-decoration:none">'
        '🔌 Setup Extension</a>'
        '</div>'
    )

    # ── Suggested accounts (from email scan, not yet connected) ─────────────────
    _site_info = {k: (n, ic, col) for k, n, ic, col, _ in SUPPORTED_SITES}
    _pending_sugg = db.execute(
        "SELECT site_key, display_name FROM email_suggestions "
        "WHERE user_id=? AND added=1 AND dismissed=0 ORDER BY email_count DESC",
        (uid,)
    ).fetchall()
    _pending_not_connected = [
        r for r in _pending_sugg if r["site_key"] not in connected_sources
    ]
    if _pending_not_connected:
        _sugg_items_html = ""
        for r in _pending_not_connected:
            sk   = r["site_key"]
            dname = r["display_name"]
            info = _site_info.get(sk)
            icon_s  = info[1] if info else "🌐"
            color_s = info[2] if info else "#e5e7eb"
            name_s  = info[0] if info else dname
            _sugg_items_html += (
                f'<div class="sugg-row" data-sk="{he(sk)}" style="display:flex;align-items:center;gap:10px;padding:8px 0;'
                f'border-bottom:1px solid #f0ede8">'
                f'<div style="width:28px;height:28px;border-radius:6px;background:{he(color_s)};'
                f'display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0">{icon_s}</div>'
                f'<div style="flex:1;font-size:13px;font-weight:500;color:#1c1917">{he(name_s)}</div>'
                f'<button onclick="dashOpenCredForm(\'{he(sk)}\',\'{he(name_s)}\',\'{icon_s}\',\'{he(color_s)}\')" '
                f'style="padding:4px 10px;border-radius:6px;border:1px solid #c7d2fe;background:#eef2ff;'
                f'font-size:11px;font-weight:600;color:#4338ca;cursor:pointer;font-family:inherit;flex-shrink:0">'
                f'Connect</button>'
                f'<button onclick="dismissSuggRow(this)" '
                f'style="background:none;border:none;color:#d1d5db;font-size:14px;cursor:pointer;padding:2px 4px;flex-shrink:0" '
                f'title="Remove from list">✕</button>'
                f'</div>'
            )
        _n_sugg = len(_pending_not_connected)
        _sugg_label = f"{_n_sugg} account{'s' if _n_sugg != 1 else ''} ready to connect"
        _sugg_section = (
            f'<div id="sugg-section" style="background:#fff;border:1.5px solid #e0e7ff;border-radius:12px;'
            f'padding:16px 20px;margin-bottom:20px">'
            f'<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">'
            f'<div style="font-size:13px;font-weight:700;color:#3730a3">📬 {he(_sugg_label)} from your email scan</div>'
            f'<div style="display:flex;gap:10px;align-items:center">'
            f'<a href="/email-scan" style="font-size:11px;color:#6366f1;text-decoration:none">Edit list</a>'
            f'<button onclick="dismissAllSugg()" style="font-size:11px;color:#9ca3af;background:none;'
            f'border:none;cursor:pointer;font-family:inherit">Dismiss all</button>'
            f'</div></div>'
            f'<div id="sugg-list" style="max-height:320px;overflow-y:auto">{_sugg_items_html}</div>'
            f'<script>'
            f'function dismissSuggRow(btn){{'
            f'  var row=btn.closest(".sugg-row");'
            f'  var sk=row?row.dataset.sk:"";'
            f'  if(sk)fetch("/api/email/suggestions/dismiss",{{method:"POST",headers:{{"Content-Type":"application/json"}},'
            f'  body:JSON.stringify({{site_key:sk}})}});'
            f'  if(row){{row.style.opacity="0";row.style.transition="opacity .2s";'
            f'  setTimeout(function(){{row.remove();'
            f'    var lst=document.getElementById("sugg-list");'
            f'    if(lst&&!lst.querySelector(".sugg-row")){{document.getElementById("sugg-section").remove();}}'
            f'  }},210);}}'
            f'}}'
            f'function dismissAllSugg(){{'
            f'  document.querySelectorAll("#sugg-list .sugg-row").forEach(function(row){{'
            f'    var sk=row.dataset.sk;'
            f'    if(sk)fetch("/api/email/suggestions/dismiss",{{method:"POST",headers:{{"Content-Type":"application/json"}},'
            f'    body:JSON.stringify({{site_key:sk}})}});'
            f'  }});'
            f'  var sec=document.getElementById("sugg-section");'
            f'  if(sec){{sec.style.opacity="0";sec.style.transition="opacity .2s";setTimeout(function(){{sec.remove();}},210);}}'
            f'}}'
            f'</script>'
            f'</div>'
        )
        account_data_html = _sugg_section + account_data_html

    # Compute total tracked value across all accounts
    total_value = 0.0
    value_items = []  # list of (source_display, field_label, value_str, dollar_val, methodology)
    for cat in _cat_order:
        for src, display_name_v, icon_v, color_v in _cat_map[cat]:
            row_v = synced_map.get(src)
            if not row_v:
                continue
            data_v = decrypt_account_data(user["id"], row_v["data_enc"] or "")
            items_v = data_v.get("items", []) or data_v.get("ai_items", []) or []
            _bf_dirty = False
            for _bi in items_v:
                # Reclassify if missing or stale "other" (catches items from older classifier versions)
                if not _bi.get("_type") or _bi.get("_type") == "other":
                    _bi["_type"] = classify_benefit(_bi.get("label",""), str(_bi.get("value","")), row_v["source"])
                    _bf_dirty = True
            if _bf_dirty:
                data_v["items"] = items_v
                try:
                    _bfdb = get_db()
                    _bfdb.execute("UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
                        (encrypt_account_data(user["id"], data_v), user["id"], row_v["source"]))
                    _bfdb.commit()
                except Exception: pass
            if src in discovered_by_source:
                disc_v = discovered_by_source[src]
                items_v = [
                    {"key": f["key"], "label": f["label"], "value": f.get("value", "")}
                    for f in disc_v.get("fields", [])
                    if f.get("key") in disc_v.get("enabled", set())
                ]
                # Classify discovered items — they have no _type from the discovery pipeline
                for _di in items_v:
                    _di["_type"] = classify_benefit(_di.get("label",""), str(_di.get("value","")), row_v["source"])
            for it in items_v:
                _rs, _rf = _relevance_score(it.get("key",""), it.get("label",""), str(it.get("value","")))
                _raw_val = _rf.get("value_factor", 1.0) * 300.0  # default 1.0 when using inline fallback
                if _raw_val > 0:
                    total_value += _raw_val
                    value_items.append((display_name_v, it.get("label",""), str(it.get("value","")), _rs, "relevance", it.get("_type","other")))
    # Get latest intent for context-aware sorting + load 30-day intent summary
    _latest_intent = get_db().execute(
        "SELECT intent_type FROM intent_history WHERE user_id=? ORDER BY detected_at DESC LIMIT 1",
        (session["user_id"],)
    ).fetchone()
    _sort_context = _latest_intent["intent_type"] if _latest_intent else None
    _dash_user_intent: dict = {}
    _dash_type_affinity: dict = {}
    try:
        import json as _json_dui
        _ui_r = get_db().execute(
            "SELECT intent_summary, type_affinity FROM users WHERE id=?", (session["user_id"],)
        ).fetchone()
        if _ui_r and _ui_r["intent_summary"]:
            _dash_user_intent = _json_dui.loads(_ui_r["intent_summary"])
        if _ui_r and _ui_r["type_affinity"]:
            _dash_type_affinity = _json_dui.loads(_ui_r["type_affinity"])
    except Exception:
        pass
    # Load user type corrections for dashboard surfaces
    _dash_corrections = _get_user_corrections(session["user_id"])
    value_items.sort(key=lambda x: -_relevance_score(
        "", x[1], x[2], context=_sort_context
    )[0])

    # ── LAYER 1: Hero section ─────────────────────────────────────────────────
    # Greeting is resolved client-side so it matches the user's local timezone
    _greeting = "Good day"   # placeholder — JS will overwrite immediately
    _first_name = (user["preferred_name"] or "").strip() or \
                  ((user["email"] or "").split("@")[0].split(".")[0] or "").capitalize() or "there"
    _account_count = len(connected_sources)

    # ── Action items: persistent, from action_items table + login_required accounts ──
    _open_items = []
    try:
        _open_items = _open_action_items(uid)
    except Exception:
        pass
    # Inject login-required accounts as top-priority (transient — not persisted)
    _lr_items = []
    for _src, _dname, _ic, _cl in [
        item for cat in _cat_order for item in _cat_map[cat]
        if synced_map.get(item[0]) and
           (synced_map[item[0]].get("sync_status") or "") == "login_required"
    ]:
        _lr_items.append({
            "id": None, "source": _src, "label": "Login required",
            "value": f"{_dname} — log in to re-sync",
            "btype": "login_required", "urgency": "urgent", "days_left": None,
        })
    _all_action_items = _lr_items + _open_items

    # Hero "Today's focus" line — use first urgent item
    _top_action = next((i for i in _all_action_items if i.get("urgency") == "urgent"), None) \
               or ((_all_action_items or [None])[0])
    if _top_action:
        _focus_label = _top_action.get("label", "")
        _focus_val   = _top_action.get("value", "")
        _focus_msg   = f"{_focus_label}: {_focus_val}" if _focus_label else _focus_val
        _focus_html = (
            f'<div style="margin-top:12px;background:#fef3c7;border-left:3px solid #f59e0b;'
            f'border-radius:0 6px 6px 0;padding:8px 12px">'
            f'<div style="font-size:11px;font-weight:600;color:#92400e;text-transform:uppercase;'
            f'letter-spacing:.05em;margin-bottom:2px">Today\'s focus</div>'
            f'<div style="font-size:13px;color:#78350f">{he(_focus_msg)}</div>'
            f'</div>'
        )
    else:
        _focus_html = ""

    # Keep _inline_reminders alias for the attention status line in greeting
    _inline_reminders = _all_action_items[:5]

    # Build named benefit bullets for hero — lead with actual value, not aggregate stats
    import re as _re_hero
    import datetime as _re_hero_dt

    def _parse_exp_days_hero(label_str, val_str):
        """Quick expiry parser for hero bullets."""
        combined = f"{label_str} {val_str}"
        # MM/DD/YYYY
        m = _re_hero.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', combined)
        if m:
            try:
                mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if yr < 100: yr += 2000
                return (_re_hero_dt.date(yr, mo, da) - _re_hero_dt.date.today()).days
            except Exception: pass
        # "expires/exp Jan 2027" style
        m2 = _re_hero.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{4})',
                              combined, _re_hero.I)
        if m2:
            _mm = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
                   'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
            try:
                mo = _mm[m2.group(1)[:3].lower()]
                yr = int(m2.group(2))
                return (_re_hero_dt.date(yr, mo, 1) - _re_hero_dt.date.today()).days
            except Exception: pass
        return None

    def _hero_icon(label_lc):
        if any(k in label_lc for k in ['companion', 'certificate', 'cert', 'free night', 'award night']): return '🎫'
        if any(k in label_lc for k in ['upgrade', 'global upgrade']): return '⬆'
        if any(k in label_lc for k in ['miles', 'points', 'rapid rewards', 'skypass', 'mileage']): return '✈'
        if any(k in label_lc for k in ['credit', 'voucher', 'ecredit']): return '💳'
        if any(k in label_lc for k in ['status', 'medallion', 'elite', 'gold', 'platinum', 'diamond', 'globalist', 'sapphire', 'titanium']): return '⭐'
        if any(k in label_lc for k in ['night', 'hotel']): return '🏨'
        return '•'

    # Priority: 1) expiring certs/credits, 2) certs/credits no expiry
    _hero_candidates = []
    for _disp, _lbl, _val, _rs_v, _meth_v, _btype in value_items:
        _vk = _val.strip()
        if not _vk or _vk in {'0', '—', '-', 'N/A', 'None', 'TBD'}: continue
        _exp = _parse_exp_days_hero(_lbl, _val)
        # Apply user correction if one exists for this field
        _fk_hero = f"{_disp}::{_lbl}"
        _btype = _dash_corrections.get(_fk_hero, _btype)
        # Use canonical type from tuple — no keyword re-scan
        if not is_actionable(_btype): continue
        # Skip zero-value items
        import re as _re_prog_hero
        _lead_nums = _re_prog_hero.findall(r'[\d,]+', _vk)
        if _lead_nums and int(_lead_nums[0].replace(',','')) == 0: continue
        # Score via unified opportunity engine (value + expiry + intent + rarity)
        _h_score_item = {"label": _lbl, "value": _val, "btype": _btype, "days_left": _exp}
        _priority = score_opportunity(_h_score_item, user_intent=_dash_user_intent, source=_disp,
                                      user_type_affinity=_dash_type_affinity)
        _hero_candidates.append((_priority, _exp or 9999, _disp, _lbl, _val, _exp, _btype))
    _hero_candidates.sort(key=lambda x: (-x[0], x[1]))

    # Load archived benefits and filter them out of hero candidates
    _arch_rows = db.execute(
        "SELECT source, label FROM archived_benefits WHERE user_id=?", (user["id"],)
    ).fetchall()
    _archived_keys = {(r["source"], r["label"]) for r in _arch_rows}
    _archived_list  = [{"source": r["source"], "label": r["label"]} for r in _arch_rows]
    _hero_candidates = [c for c in _hero_candidates if (c[2], c[3]) not in _archived_keys]

    # Build display_name → synced_ago lookup for evidence lines
    import datetime as _hdt_ev
    def _hero_synced_ago(synced_at_str):
        if not synced_at_str: return ""
        try:
            _dt2 = _hdt_ev.datetime.fromisoformat(synced_at_str.replace("Z",""))
            _h2  = int((_hdt_ev.datetime.utcnow() - _dt2).total_seconds() / 3600)
            if _h2 < 1:  return "just now"
            if _h2 < 24: return f"{_h2}h ago"
            return f"{_h2 // 24}d ago"
        except Exception: return ""
    _synced_ago_by_disp = {r["display_name"]: _hero_synced_ago(r["synced_at"]) for r in acct_rows}

    _hero_bullets_html = ""
    _seen_hero = set()
    import json as _json_hero
    import datetime as _hdt
    for _pr, _, _hdisp, _hlbl, _hval, _hexp, _btype in _hero_candidates[:5]:
        _dedup_key = (_hdisp, _hlbl[:30])
        if _dedup_key in _seen_hero: continue
        _seen_hero.add(_dedup_key)
        _icon = _hero_icon(_hlbl.lower())
        # Color by type
        _lk2 = _hlbl.lower()
        if any(k in _lk2 for k in ['credit', 'voucher']): _bullet_color = "#059669"
        elif any(k in _lk2 for k in ['cert', 'companion', 'upgrade', 'free night']): _bullet_color = "#2563eb"
        elif any(k in _lk2 for k in ['status', 'medallion', 'elite', 'globalist']): _bullet_color = "#7c3aed"
        else: _bullet_color = "#374151"
        # Sub-line: account name + expiry
        _sub_parts = [f'<span style="color:#9ca3af">{he(_hdisp)}</span>']
        if _hexp is not None and _hexp >= 0:
            _exp_date = _hdt.date.today() + _hdt.timedelta(days=_hexp)
            if _hexp <= 30:
                _sub_parts.append(f'<span style="color:#dc2626;font-weight:600">Expires in {_hexp}d</span>')
            else:
                _sub_parts.append(f'<span style="color:#d97706">Expires {_exp_date.strftime("%b %Y")}</span>')
        _sub_html = ' · '.join(_sub_parts)
        # Show value only if meaningful (not generic "Available" / "Active")
        _skip_vals = {'available', 'active', 'yes', 'enabled', 'valid', 'earned', ''}
        _show_val = ""
        if _hval and _hval.lower().strip() not in _skip_vals:
            _show_val = f'<span style="font-size:13px;color:#6b7280;margin-left:6px">{he(_hval)}</span>'
        # Why-shown line — context-aware one-liner above the evidence trail
        _why_line_txt = _why_shown(_btype, _hexp, _hdisp)
        _why_line_html = (
            f'<div style="font-size:11px;color:#6b7280;margin-top:2px;line-height:1.4">'
            f'{he(_why_line_txt)}</div>'
        ) if _why_line_txt else ""

        # Evidence line — source + freshness + confidence
        _hsynced_ago = _synced_ago_by_disp.get(_hdisp, "")
        _hstale = bool(_hsynced_ago and "d" in _hsynced_ago and int(_hsynced_ago.split("d")[0].strip() or 0) >= 2)
        _ev_parts = [f'Found in {he(_hdisp)}']
        if _hsynced_ago:
            _ev_color = "#f97316" if _hstale else "#d1d5db"
            _ev_parts.append(f'<span style="color:{_ev_color}">{he(_hsynced_ago)}</span>')
        _ev_parts.append('<span style="color:#d1d5db">High confidence</span>')
        _evidence_line = (
            '<div style="font-size:10px;color:#d1d5db;margin-top:3px;line-height:1.5">'
            + ' · '.join(_ev_parts) +
            '</div>'
        )
        # JSON data for benefit detail drawer
        _fk_hero_bd = f"{_hdisp}::{_hlbl}"
        _btype_hero_bd = _dash_corrections.get(_fk_hero_bd, _btype)
        _corrected_hero = _fk_hero_bd in _dash_corrections
        _bd_data = _json_hero.dumps({
            "label": _hlbl, "account": _hdisp,
            "value": _hval, "icon": _icon, "expDays": _hexp,
            "synced_ago": _hsynced_ago, "confidence": "High",
            "field_key": _fk_hero_bd, "btype": _btype_hero_bd,
            "corrected": _corrected_hero
        }).replace("'", "&#39;")
        _hero_bullets_html += (
            f'<div style="display:flex;gap:10px;padding:8px 4px;cursor:pointer;border-radius:7px;'
            f'transition:background 0.1s" onmouseover="this.style.background=\'#f5f5f5\'" '
            f'onmouseout="this.style.background=\'\'" '
            f'onclick="openBenefitDrawer(this)" data-benefit=\'{_bd_data}\'>'
            f'<span style="font-size:18px;flex-shrink:0;margin-top:1px;line-height:1.3">{_icon}</span>'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:14px;font-weight:600;color:{_bullet_color};line-height:1.3">'
            f'{he(_hlbl)}{_show_val}</div>'
            f'<div style="font-size:12px;margin-top:2px;line-height:1.4">{_sub_html}</div>'
            + _why_line_html
            + f'{_evidence_line}'
            f'</div>'
            f'</div>'
        )

    if _hero_bullets_html:
        _available_label = (
            '<h2 style="font-size:11px;font-weight:700;color:#9ca3af;margin:0 0 8px;'
            'text-transform:uppercase;letter-spacing:.06em">Available Now</h2>'
        )
        _hero_value_block = _available_label + _hero_bullets_html
    else:
        # No benefit data yet — show a soft prompt
        if _account_count > 0:
            _hero_value_block = (
                f'<div style="margin-top:10px;font-size:13px;color:#9ca3af">'
                f'Watching {_account_count} account{"s" if _account_count != 1 else ""} — '
                f'sync to see your benefits.</div>'
            )
        else:
            _hero_value_block = ''

    # Value-first sub-line: lead with what the user has, not counts
    def _article(lbl):
        """Return 'a '/'an '/'' — empty string for plural labels (last word ends in s, not ss)."""
        if not lbl:
            return ""
        last_word = lbl.rsplit(None, 1)[-1].lower().rstrip(".")
        if last_word.endswith("s") and not last_word.endswith("ss"):
            return ""
        return "an " if lbl[0].lower() in "aeiou" else "a "

    _n_hc = len(_hero_candidates)
    if _n_hc >= 1:
        _th_lbl   = _hero_candidates[0][3]
        _th_btype = _hero_candidates[0][6]
        _art = _article(_th_lbl)
        if _n_hc == 1:
            _value_lead = f"You have {_art}{he(_th_lbl)} available right now."
        elif _n_hc == 2:
            _th2_lbl = _hero_candidates[1][3]
            _art2 = _article(_th2_lbl)
            _value_lead = f"You have {_art}{he(_th_lbl)} and {_art2}{he(_th2_lbl)} available."
        else:
            _value_lead = f"You have {_n_hc} benefits available, including {_art}{he(_th_lbl)}."
    elif _account_count > 0:
        _n_statuses = sum(1 for _, _, _, _, _, bt in value_items if bt == "elite_status")
        if _n_statuses:
            _value_lead = f'{_account_count} account{"s" if _account_count != 1 else ""} synced.'
        else:
            _value_lead = f'{_account_count} account{"s" if _account_count != 1 else ""} connected.'
    else:
        _value_lead = "No accounts connected yet."

    # Greeting + value-first header
    hero_section_html = (
        f'<div style="padding-bottom:20px;margin-bottom:20px;border-bottom:1px solid #e5e0da">'
        f'<div style="font-size:20px;font-weight:700;color:#1c1917" id="hero-greeting">'
        f'Hello, {he(_first_name)}'
        f'</div>'
        f'<script>'
        f'(function(){{'
        f'  var h=new Date().getHours();'
        f'  var g=h<12?"Good morning":h<17?"Good afternoon":"Good evening";'
        f'  var el=document.getElementById("hero-greeting");'
        f'  if(el) el.textContent=g+", {he(_first_name)}";'
        f'}})();'
        f'</script>'
        f'<div style="font-size:14px;color:#374151;margin-top:6px;line-height:1.5">{_value_lead}</div>'
        f'</div>'
    )

    # ── INSIGHTS: Available Now — prominent section, benefits as primary objects ─
    # Shows top scored certs/credits as a clean list. Each item is clickable
    # and opens the benefit detail drawer. Ordered by score (expiry + value + intent).
    import json as _json_ins
    _ins_rows_html = ""
    _ins_seen = set()
    for _ip, _, _idisp, _ilbl, _ival, _iexp, _ibtype in _hero_candidates[:5]:
        _idk = (_idisp, _ilbl[:30])
        if _idk in _ins_seen: continue
        _ins_seen.add(_idk)
        _iicon = _hero_icon(_ilbl.lower())
        if _iicon == '•':
            _iicon = {'certificate':'🎫','travel_credit':'✈️','cash_credit':'💳',
                      'elite_status':'⭐','points_balance':'✈️','upgrade_cert':'⬆️'}.get(_ibtype,'•')
        # Value display
        _iskip = {'available','active','yes','enabled','valid','earned',''}
        _ival_show = _ival.strip() if _ival.strip() and _ival.strip().lower() not in _iskip else ""
        # Sub-line: "through [source]" + expiry chip
        _isub_parts = [f'through {he(_idisp)}']
        _iexp_html = ""
        if _iexp is not None and _iexp >= 0:
            import datetime as _idt
            if _iexp <= 14:
                _iexp_html = (
                    f'<span style="display:inline-block;margin-top:4px;font-size:11px;font-weight:600;'
                    f'color:#dc2626;background:#fee2e2;border-radius:4px;padding:1px 6px">'
                    f'Expires in {_iexp}d</span>'
                )
            elif _iexp <= 60:
                _iexp_html = (
                    f'<span style="display:inline-block;margin-top:4px;font-size:11px;'
                    f'color:#d97706;background:#fef3c7;border-radius:4px;padding:1px 6px">'
                    f'Exp {(_idt.date.today() + _idt.timedelta(days=_iexp)).strftime("%b %Y")}</span>'
                )
        # Label color: certs blue, credits green
        _ilbl_color = "#1d4ed8" if _ibtype == "certificate" else "#059669" if _ibtype in ("travel_credit","cash_credit") else "#1c1917"
        # Drawer data
        _ifk = f"{_idisp}::{_ilbl}"
        _ibd = _json_ins.dumps({
            "label": _ilbl, "account": _idisp, "value": _ival,
            "icon": _iicon, "expDays": _iexp, "field_key": _ifk, "btype": _ibtype,
            "corrected": _ifk in _dash_corrections
        }).replace("'", "&#39;")
        _iarch_src = "'" + _idisp.replace("\\", "\\\\").replace("'", "\\'") + "'"
        _iarch_lbl = "'" + _ilbl.replace("\\", "\\\\").replace("'", "\\'") + "'"
        _ins_rows_html += (
            f'<div style="display:flex;gap:14px;align-items:flex-start;padding:13px 8px;'
            f'border-bottom:1px solid #e8e3de;border-radius:8px;cursor:pointer;'
            f'transition:background 0.1s" '
            f'onclick="openBenefitDrawer(this)" data-benefit=\'{_ibd}\' '
            f'onmouseover="this.style.background=\'#edece9\';this.querySelector(\'.arch-btn\').style.opacity=\'1\'" '
            f'onmouseout="this.style.background=\'\';this.querySelector(\'.arch-btn\').style.opacity=\'0\'">'
            f'<span style="font-size:24px;flex-shrink:0;line-height:1.1;margin-top:2px">{_iicon}</span>'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:15px;font-weight:700;color:{_ilbl_color};line-height:1.3">'
            f'{he(_ilbl)}'
            + (f'<span style="font-size:13px;font-weight:500;color:#4b5563;margin-left:7px">{he(_ival_show)}</span>' if _ival_show else '')
            + f'</div>'
            f'<div style="font-size:12px;color:#9ca3af;margin-top:2px">{" · ".join(_isub_parts)}</div>'
            + (f'<div>{_iexp_html}</div>' if _iexp_html else '')
            + f'</div>'
            f'<button class="arch-btn" onclick="archiveBenefit({_iarch_src},{_iarch_lbl},this)" '
            f'title="Archive — hide from this list" '
            f'style="flex-shrink:0;background:none;border:none;cursor:pointer;color:#9ca3af;'
            f'font-size:11px;font-weight:500;padding:4px 6px;border-radius:4px;align-self:center;'
            f'opacity:0;transition:opacity 0.15s;white-space:nowrap">Archive</button>'
            f'</div>'
        )
    import json as _json_arch
    _arch_js = f'<script>var _ARCHIVED_BENEFITS={_json_arch.dumps(_archived_list)};</script>'
    _arch_count = len(_archived_list)
    _arch_footer = (
        f'<div id="arch-footer-wrap" style="margin-top:6px;text-align:right;'
        + ('' if _arch_count else 'display:none;')
        + f'">'
        f'<button id="arch-toggle-btn" onclick="toggleArchivedPanel()" '
        f'style="background:none;border:none;cursor:pointer;font-size:11px;color:#9ca3af;padding:0">'
        f'↩ {_arch_count} archived</button></div>'
        f'<div id="arch-inline-panel" style="display:none;border-top:1px solid #f0ece8;margin-top:8px;padding-top:8px">'
        f'<div id="arch-inline-list"></div></div>'
    )
    if _ins_rows_html:
        insights_html = (
            _arch_js +
            f'<div style="margin-bottom:24px">'
            f'<h2 style="font-size:10px;font-weight:700;color:#9ca3af;margin:0 0 2px;'
            f'text-transform:uppercase;letter-spacing:.08em">Available Now</h2>'
            + _ins_rows_html
            + _arch_footer +
            f'</div>'
        )
    elif _arch_count:
        # All items archived — still show the "X archived" link so the user can restore
        insights_html = (
            _arch_js +
            f'<div style="margin-bottom:24px">'
            f'<h2 style="font-size:10px;font-weight:700;color:#9ca3af;margin:0 0 8px;'
            f'text-transform:uppercase;letter-spacing:.08em">Available Now</h2>'
            f'<div style="font-size:13px;color:#9ca3af;padding:4px 0">All items archived. '
            f'<button onclick="toggleArchivedPanel()" style="background:none;border:none;cursor:pointer;'
            f'font-size:13px;color:#6366f1;text-decoration:underline;padding:0">View archived</button></div>'
            f'<div id="arch-footer-wrap" style="display:none"></div>'
            f'<div id="arch-inline-panel" style="display:none;border-top:1px solid #f0ece8;margin-top:8px;padding-top:8px">'
            f'<div id="arch-inline-list"></div></div>'
            f'</div>'
        )
    else:
        insights_html = ""
    # ── END INSIGHTS ─────────────────────────────────────────────────────────

    # ── LAYER 2: Action Center (persistent, dismissible) ─────────────────────
    def _ai_urgency_icon(urgency):
        return "🔴" if urgency == "urgent" else "🟡"

    _csrf_tok = get_csrf_token()
    _ac_items_html = ""
    for _ai in _all_action_items[:6]:
        _ai_id       = _ai.get("id")          # None for login_required
        _ai_urgency  = _ai.get("urgency", "info")
        _ai_urg_clr  = "#dc2626" if _ai_urgency == "urgent" else "#d97706"
        _ai_icon     = _ai_urgency_icon(_ai_urgency)
        _ai_label    = _ai.get("label", "")
        _ai_val      = _ai.get("value", "")
        _ai_btype    = _ai.get("btype", "")
        _ai_days     = _ai.get("days_left")
        if _ai_btype == "login_required":
            _ai_why = "Re-authenticate to continue syncing"
        elif _ai_days is not None and _ai_days < 0:
            _ai_why = "Expired"
        else:
            _ai_why = _why_shown(_ai_btype, _ai_days)
        _ai_disp_lbl = _ai_val if not _ai_label or _ai_label == "Login required" else f"{_ai_label}: {_ai_val}"

        # Action buttons — only for persisted items (not transient login_required)
        if _ai_id is not None:
            # Shared: remove item from DOM, hide wrap if empty
            _rm_js = (
                f"document.getElementById('ai-{_ai_id}').remove();"
                f"if(!document.querySelector('[id^=\"ai-\"]'))document.getElementById('action-center-wrap').remove();"
            )
            _done_js = (
                f"fetch('/api/action-items/{_ai_id}/complete',{{"
                f"method:'POST',headers:{{'X-CSRF-Token':'{_csrf_tok}','Content-Type':'application/json'}},"
                f"credentials:'same-origin'}}).then(r=>r.ok&&({_rm_js}))"
            )
            _dismiss_js = (
                f"fetch('/api/action-items/{_ai_id}/dismiss',{{"
                f"method:'POST',headers:{{'X-CSRF-Token':'{_csrf_tok}','Content-Type':'application/json'}},"
                f"credentials:'same-origin'}}).then(r=>r.ok&&({_rm_js}))"
            )
            _snooze_js = (
                f"fetch('/api/action-items/{_ai_id}/snooze',{{"
                f"method:'POST',headers:{{'X-CSRF-Token':'{_csrf_tok}','Content-Type':'application/json'}},"
                f"body:JSON.stringify({{days:7}}),credentials:'same-origin'}})"
                f".then(r=>r.ok&&({_rm_js}))"
            )
            _action_btns = (
                f'<div style="display:flex;gap:6px;margin-top:6px">'
                f'<button onclick="{_done_js}" style="font-size:11px;font-weight:600;color:#fff;'
                f'background:#16a34a;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;'
                f'line-height:1.5" title="Mark as done — saves as a signal">✓ Done</button>'
                f'<button onclick="{_snooze_js}" style="font-size:11px;color:#6b7280;background:none;'
                f'border:1px solid #e5e7eb;border-radius:4px;padding:3px 8px;cursor:pointer;'
                f'line-height:1.5" title="Hide for 7 days">Snooze 7d</button>'
                f'<button onclick="{_dismiss_js}" style="font-size:11px;color:#9ca3af;background:none;'
                f'border:none;padding:3px 4px;cursor:pointer;line-height:1.5" title="Not relevant">✕</button>'
                f'</div>'
            )
            _item_id_attr = f'id="ai-{_ai_id}"'
        else:
            _action_btns  = ""
            _item_id_attr = ""

        _ac_items_html += (
            f'<div {_item_id_attr} style="padding:10px 0;border-bottom:1px solid #fef3c7;'
            f'display:flex;align-items:flex-start;gap:10px">'
            f'<span style="font-size:15px;flex-shrink:0;margin-top:2px">{_ai_icon}</span>'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:13px;font-weight:500;color:#111;line-height:1.35">{he(_ai_disp_lbl)}</div>'
            + (f'<div style="font-size:11px;color:{_ai_urg_clr};margin-top:1px">{he(_ai_why)}</div>' if _ai_why else '')
            + _action_btns
            + f'</div>'
            f'</div>'
        )

    # action_center_html suppressed — action items merged into AVAILABLE section below
    action_center_html = ""

    # ── LAYER 2b: Recently Discovered feed ───────────────────────────────────
    import datetime as _dtrd
    _cutoff = (_dtrd.datetime.utcnow() - _dtrd.timedelta(days=14)).isoformat()
    try:
        # Fetch extra rows so we can detect first-sync floods
        _recent_rows = get_db().execute(
            "SELECT source, field_label, new_value, old_value, changed_at FROM field_history "
            "WHERE user_id=? AND old_value IS NULL AND changed_at > ? "
            "ORDER BY changed_at DESC LIMIT 50",
            (uid, _cutoff)
        ).fetchall()
    except Exception:
        _recent_rows = []

    # Build a flat source→display_name lookup from _cat_map
    _src_display_lookup = {}
    for _cat_vals in _cat_map.values():
        for _sk, _sn, _si, _sc in _cat_vals:
            _src_display_lookup[_sk] = _sn

    def _source_display_name(src):
        return _src_display_lookup.get(src) or src.replace("_", " ").title()

    # ── RECENTLY CHANGED: account-level activity feed (compact, insight-focused) ──
    recently_found_html = ""
    if _recent_rows:
        _now_dt = _dtrd.datetime.utcnow()
        def _rel_time(ts_str):
            try:
                _dt = _dtrd.datetime.fromisoformat(ts_str.replace("Z",""))
                _h  = int((_now_dt - _dt).total_seconds() / 3600)
                if _h < 1:  return "just now"
                if _h < 24: return f"{_h}h ago"
                _d  = _h // 24
                if _d == 1: return "yesterday"
                return f"{_d} days ago"
            except Exception: return ""

        # Group rows by source — keep the most-recent change per source
        _src_latest: dict = {}
        for _rr in _recent_rows:
            _s = _rr["source"]
            if _s not in _src_latest:
                _src_latest[_s] = _rr

        # Build compact lines: "Delta — Companion Certificate found 9h ago"
        # Prefer high-value labels (certs, credits, status) over generic fields
        _VALUE_KEYWORDS = ('certificate', 'cert', 'companion', 'free night', 'credit',
                           'ecredit', 'voucher', 'upgrade', 'status', 'medallion',
                           'elite', 'diamond', 'platinum', 'gold', 'globalist', 'miles', 'points')
        _rc_lines = []
        for _s, _rr in list(_src_latest.items())[:5]:
            _disp_rc = _source_display_name(_s)
            _lbl_rc  = _rr["field_label"] or ""
            _ago_rc  = _rel_time(_rr["changed_at"] or "")
            _is_new  = not _rr["old_value"]   # first-time discovery vs update
            _verb    = "found" if _is_new else "updated"
            _is_valuable = any(k in _lbl_rc.lower() for k in _VALUE_KEYWORDS)
            # Show the label only if it's a high-value field; otherwise say "synced"
            if _is_valuable and _lbl_rc:
                _line_text = f'{he(_disp_rc)} — {he(_lbl_rc)} {_verb}'
            else:
                _line_text = f'{he(_disp_rc)} synced'
            _rc_lines.append(
                f'<div style="padding:6px 0;border-bottom:1px solid #f3f4f6;display:flex;'
                f'align-items:baseline;justify-content:space-between;gap:8px">'
                f'<div style="font-size:13px;color:#374151;line-height:1.4">{_line_text}</div>'
                f'<div style="font-size:11px;color:#9ca3af;flex-shrink:0">{_ago_rc}</div>'
                f'</div>'
            )

        if _rc_lines:
            recently_found_html = ""  # suppressed — data in account cards

    # Pre-check whether TOP BENEFITS will render — avoids duplicating certs/credits in Use Soon
    _will_have_top_benefits = any(
        _btype_q in ("certificate", "travel_credit", "cash_credit", "elite_status")
        and _val_q.strip() and _val_q.strip() not in {'0', '—', '-', 'N/A', 'None', 'TBD'}
        for _, _lbl_q, _val_q, _rs_q, _meth_q, _btype_q in value_items
    )

    # ── LAYER 3: Don't forget section (no dollar amounts) ────────────────────
    if value_items:
        import re as _vc_re

        def _use_soon_eligible(label: str, val_str: str) -> bool:
            """Exclude progress trackers and zero/empty values from Use Soon."""
            lc = label.lower()
            vs = val_str.strip()
            if 'progress' in lc: return False          # progress tracker, not a usable benefit
            if not vs or vs in {'0', '—', '-', '–', 'N/A', 'n/a', 'None', 'TBD'}: return False
            if _vc_re.match(r'^0\s+of\s+', vs): return False  # "0 of 135,000"
            if _vc_re.match(r'^\d{7,}$', vs.replace(',', '')): return False  # bare account number
            return True

        # Group by canonical _type — no keyword re-scan
        # Skip certs/credits in Use Soon when TOP BENEFITS already shows them above
        credit_items = [] if _will_have_top_benefits else [
            (disp, label, val_str)
            for disp, label, val_str, dval, method, btype_vs in value_items
            if btype_vs in ("travel_credit", "cash_credit") and _use_soon_eligible(label, val_str)
        ]
        cert_items = [] if _will_have_top_benefits else [
            (disp, label, val_str)
            for disp, label, val_str, dval, method, btype_vs in value_items
            if btype_vs == "certificate" and _use_soon_eligible(label, val_str)
        ]
        points_items = [
            (disp, label, val_str)
            for disp, label, val_str, dval, method, btype_vs in value_items
            if btype_vs == "points_balance" and _use_soon_eligible(label, val_str)
        ]

        forgotten_lines = []
        for disp, label, val_str in (credit_items + cert_items)[:6]:
            forgotten_lines.append(
                f'<div style="font-size:13px;color:#374151;padding:4px 0;'
                f'border-bottom:1px solid #f3f4f6">• {he(disp)}: {he(label)}'
                f'<span style="color:#6b7280;margin-left:6px">{he(val_str)}</span></div>'
            )

        points_lines = []
        for disp, label, val_str in points_items[:4]:
            points_lines.append(
                f'<div style="font-size:13px;color:#374151;padding:4px 0;'
                f'border-bottom:1px solid #f3f4f6">• {he(disp)}: {he(label)}'
                f'<span style="color:#6b7280;margin-left:6px">{he(val_str)}</span></div>'
            )

        value_center_html = ""  # suppressed — data in account cards
        value_banner = ""
    else:
        value_center_html = ""
        value_banner = ""

    # ── TOP BENEFITS: curated named benefit cards (replaces weak Opportunities) ─
    import re as _tb_re
    import datetime as _tb_dt

    def _tb_exp_days(label, val):
        combined = f"{label} {val}"
        m = _tb_re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', combined)
        if m:
            try:
                mo, da, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if yr < 100: yr += 2000
                return (_tb_dt.date(yr, mo, da) - _tb_dt.date.today()).days
            except Exception: pass
        m2 = _tb_re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{4})',
                            combined, _tb_re.I)
        if m2:
            _mm = {'jan':1,'feb':2,'mar':3,'apr':4,'may':5,'jun':6,
                   'jul':7,'aug':8,'sep':9,'oct':10,'nov':11,'dec':12}
            try:
                mo = _mm[m2.group(1)[:3].lower()]
                yr = int(m2.group(2))
                return (_tb_dt.date(yr, mo, 1) - _tb_dt.date.today()).days
            except Exception: pass
        return None

    _top_benefit_cards = []
    _progress_cards = []
    _tb_seen = set()
    import re as _tb_re2
    for _disp, _lbl, _val, _rs_tb, _meth_tb, _btype_tb in value_items:
        _vk = _val.strip()
        if not _vk or _vk in {'0', '—', '-', 'N/A', 'None', 'TBD'}: continue
        # Apply user correction if one exists for this field
        _fk_tb = f"{_disp}::{_lbl}"
        _btype_tb = _dash_corrections.get(_fk_tb, _btype_tb)
        # Use canonical type from tuple — no keyword re-scan
        if _btype_tb == "progress_toward":
            _prog_dedup = (_disp, _lbl[:35])
            if _prog_dedup not in _tb_seen:
                _tb_seen.add(_prog_dedup)
                _progress_cards.append((_disp, _lbl, _vk, _tb_exp_days(_lbl, _vk)))
            continue
        if not is_status(_btype_tb): continue  # Status section: elite_status only
        # Skip utility/telecom sources — they're not loyalty programs
        _STATUS_SKIP_SOURCES = {
            "xfinity","comcast","spectrum","cox","centurylink","att_internet",
            "pge","sdge","palo_alto_utilities","verizon","tmobile","att_wireless",
        }
        if any(s in _disp.lower().replace(" ","_") for s in _STATUS_SKIP_SOURCES) or \
           any(_disp.lower().startswith(s.replace("_"," ")) for s in _STATUS_SKIP_SOURCES):
            continue
        # Skip values that look like sentences, not tier names (>5 words)
        if len(_vk.split()) > 5:
            continue
        _dedup = (_disp, _lbl[:35])
        if _dedup in _tb_seen: continue
        _tb_seen.add(_dedup)
        _exp = _tb_exp_days(_lbl, _val)
        _tb_score_item = {"label": _lbl, "value": _val, "btype": _btype_tb, "days_left": _exp}
        _pri = score_opportunity(_tb_score_item, user_intent=_dash_user_intent, source=_disp,
                                 user_type_affinity=_dash_type_affinity)
        _top_benefit_cards.append((_pri, _exp if _exp is not None else 9999,
                                   _disp, _lbl, _val, _exp))
    _top_benefit_cards.sort(key=lambda x: (-x[0], x[1]))

    # ── PROGRESS SECTION: only show meaningful progress (≥10% or within 20% of goal) ──
    _prog_html = ""
    for _pdisp, _plbl, _pval, _pexp in _progress_cards[:4]:
        _pm = _tb_re2.search(r'(\d[\d,]*)\s*(?:of|/)\s*(\d[\d,]*)', _pval)
        if _pm:
            _pcur = int(_pm.group(1).replace(',', ''))
            _ptgt = int(_pm.group(2).replace(',', ''))
            _ppct = int(100 * _pcur / _ptgt) if _ptgt else 0
            # Skip empty/near-zero progress — it's just noise
            _near_goal = _ptgt > 0 and (_ptgt - _pcur) <= _ptgt * 0.20
            if _ppct < 10 and not _near_goal:
                continue
            _pbar = (
                f'<div style="margin-top:6px;height:3px;background:#e5e7eb;border-radius:3px">'
                f'<div style="height:3px;width:{min(_ppct,100)}%;background:#6366f1;border-radius:3px"></div>'
                f'</div>'
            )
            _pright = f'{_pcur:,} / {_ptgt:,}'
        else:
            _pbar = ""
            _pright = _pval
        _prog_html += (
            f'<div style="display:flex;align-items:flex-start;justify-content:space-between;'
            f'padding:10px 0;border-bottom:1px solid #f3f4f6">'
            f'<div style="flex:1;min-width:0;margin-right:12px">'
            f'<div style="font-size:11px;color:#9ca3af;font-weight:500">{he(_pdisp)}</div>'
            f'<div style="font-size:13px;font-weight:600;color:#374151;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis">{he(_plbl)}</div>'
            f'{_pbar}'
            f'</div>'
            f'<div style="font-size:13px;font-weight:600;color:#6366f1;flex-shrink:0;padding-top:12px">'
            f'{he(_pright)}</div>'
            f'</div>'
        )
    # STATUS section: elite_status chips ("◆ Delta Diamond · ◆ Marriott Gold")
    _status_chips_html = ""
    for _pri_sc, _, _disp_sc, _lbl_sc, _val_sc, _exp_sc in _top_benefit_cards[:12]:
        _tier_sc = _val_sc.strip() if _val_sc.strip() and _val_sc.strip().lower() not in {
            'active','yes','enabled','true','','member'} else _lbl_sc
        _tier_lc_sc = _tier_sc.lower()
        if any(k in _tier_lc_sc for k in ['diamond','globalist','titanium','executive','chairman','1k','executive platinum']):
            _chip_color, _chip_bg = "#92400e", "#fef3c7"  # amber — top tier
        elif any(k in _tier_lc_sc for k in ['platinum','gold','sapphire','status']):
            _chip_color, _chip_bg = "#5b21b6", "#f5f3ff"  # purple — mid-high
        else:
            _chip_color, _chip_bg = "#374151", "#f3f4f6"  # grey — base
        _fk_sc = f"{_disp_sc}::{_lbl_sc}"
        _btype_sc = _dash_corrections.get(_fk_sc, "elite_status")
        import json as _json_sc
        _sc_bd = _json_sc.dumps({
            "label": _lbl_sc, "account": _disp_sc, "value": _val_sc,
            "icon": "◆", "expDays": _exp_sc,
            "field_key": _fk_sc, "btype": _btype_sc,
            "corrected": _fk_sc in _dash_corrections,
        }).replace("'", "&#39;")
        _status_chips_html += (
            f'<span style="display:inline-flex;flex-direction:column;align-items:flex-start;gap:1px;'
            f'background:{_chip_bg};color:{_chip_color};font-size:12px;font-weight:600;'
            f'border-radius:12px;padding:5px 11px;cursor:pointer;white-space:nowrap" '
            f'onclick="openBenefitDrawer(this)" data-benefit=\'{_sc_bd}\'>'
            f'<span>◆ {he(_tier_sc)}</span>'
            f'<span style="font-size:10px;font-weight:400;opacity:0.65;letter-spacing:0">{he(_disp_sc)}</span>'
            f'</span>'
        )
    # Status chips are now shown on each account card — no separate "Your Status" section needed
    progress_section_html = ""

    # Status section: identity rows — tier name prominent, account below
    import json as _tb_json
    _tb_html = ""
    for _pri, _, _disp, _lbl, _val, _exp in _top_benefit_cards[:8]:
        # Tier: use value if it's a real tier name, else fall back to label
        _tier = _val.strip() if _val.strip() and _val.strip().lower() not in {'active','yes','enabled','true','','member'} else _lbl
        _fk_tb_bd = f"{_disp}::{_lbl}"
        _btype_tb_bd = _dash_corrections.get(_fk_tb_bd, "elite_status")
        _corrected_tb = _fk_tb_bd in _dash_corrections
        _tb_bd_data = _tb_json.dumps({
            "label": _lbl, "account": _disp,
            "value": _val, "icon": "", "expDays": _exp,
            "field_key": _fk_tb_bd, "btype": _btype_tb_bd,
            "corrected": _corrected_tb
        }).replace("'", "&#39;")
        # Pick diamond color by rough tier level
        _tier_lc = _tier.lower()
        if any(k in _tier_lc for k in ['diamond','globalist','titanium','executive','chairman','1k','executive platinum']):
            _diamond_color = "#b45309"  # gold-amber for top tier
        elif any(k in _tier_lc for k in ['platinum','gold','sapphire','status']):
            _diamond_color = "#6c47ff"  # purple for mid-high
        else:
            _diamond_color = "#6b7280"  # grey for base/silver
        _tb_why = _why_shown("elite_status", _exp, _disp)
        _tb_html += (
            f'<div style="display:flex;align-items:center;gap:10px;padding:7px 4px;'
            f'cursor:pointer;border-radius:7px;transition:background 0.1s" '
            f'onmouseover="this.style.background=\'#ede9f8\'" '
            f'onmouseout="this.style.background=\'\'" '
            f'onclick="openBenefitDrawer(this)" data-benefit=\'{_tb_bd_data}\'>'
            f'<div style="font-size:13px;color:{_diamond_color};flex-shrink:0;line-height:1">◆</div>'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:14px;font-weight:700;color:#1c1917;line-height:1.3;'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{he(_tier)}</div>'
            f'<div style="font-size:11px;color:#9ca3af;margin-top:1px">{he(_disp)}</div>'
            + (f'<div style="font-size:10px;color:#a78bfa;margin-top:1px">{he(_tb_why)}</div>' if _tb_why else '')
            + f'</div></div>'
        )
    # ── AVAILABLE section: hero bullets (certs/credits) + urgent action items ─────
    # Build urgent-action items inline (payment_due, renewal, login_required)
    _avail_attn_html = ""
    _csrf_tok = get_csrf_token()
    for _ai in _all_action_items[:6]:
        _ai_id      = _ai.get("id")
        _ai_urgency = _ai.get("urgency", "info")
        _ai_label   = _ai.get("label", "")
        _ai_val     = _ai.get("value", "")
        _ai_btype   = _ai.get("btype", "")
        _ai_days    = _ai.get("days_left")
        _ai_disp_lbl = _ai_val if not _ai_label or _ai_label == "Login required" else f"{_ai_label}: {_ai_val}"
        if _ai_btype == "login_required":
            _ai_why = "Re-authenticate to continue syncing"
        elif _ai_days is not None and _ai_days < 0:
            _ai_why = "Expired"
        else:
            _ai_why = _why_shown(_ai_btype, _ai_days)
        _ai_urg_clr = "#dc2626" if _ai_urgency == "urgent" else "#d97706"
        _ai_icon    = "🔴" if _ai_urgency == "urgent" else "🟡"
        # Action buttons for persisted items
        if _ai_id is not None:
            _rm_js = (
                f"document.getElementById('avail-ai-{_ai_id}').remove();"
                f"if(!document.querySelector('[id^=\"avail-ai-\"]'))document.getElementById('avail-attn-section').remove();"
            )
            _done_js = (
                f"fetch('/api/action-items/{_ai_id}/complete',{{"
                f"method:'POST',headers:{{'X-CSRF-Token':'{_csrf_tok}','Content-Type':'application/json'}},"
                f"credentials:'same-origin'}}).then(r=>r.ok&&({_rm_js}))"
            )
            _dismiss_js = (
                f"fetch('/api/action-items/{_ai_id}/dismiss',{{"
                f"method:'POST',headers:{{'X-CSRF-Token':'{_csrf_tok}','Content-Type':'application/json'}},"
                f"credentials:'same-origin'}}).then(r=>r.ok&&({_rm_js}))"
            )
            _snooze_js = (
                f"fetch('/api/action-items/{_ai_id}/snooze',{{"
                f"method:'POST',headers:{{'X-CSRF-Token':'{_csrf_tok}','Content-Type':'application/json'}},"
                f"body:JSON.stringify({{days:7}}),credentials:'same-origin'}})"
                f".then(r=>r.ok&&({_rm_js}))"
            )
            _ai_btns = (
                f'<div style="display:flex;gap:6px;margin-top:5px">'
                f'<button onclick="{_done_js}" style="font-size:11px;font-weight:600;color:#fff;'
                f'background:#16a34a;border:none;border-radius:4px;padding:3px 10px;cursor:pointer;line-height:1.5">✓ Done</button>'
                f'<button onclick="{_snooze_js}" style="font-size:11px;color:#6b7280;background:none;'
                f'border:1px solid #e5e7eb;border-radius:4px;padding:3px 8px;cursor:pointer;line-height:1.5">Snooze 7d</button>'
                f'<button onclick="{_dismiss_js}" style="font-size:11px;color:#9ca3af;background:none;'
                f'border:none;padding:3px 4px;cursor:pointer;line-height:1.5">✕</button>'
                f'</div>'
            )
            _ai_id_attr = f'id="avail-ai-{_ai_id}"'
        else:
            _ai_btns, _ai_id_attr = "", ""
        _avail_attn_html += (
            f'<div {_ai_id_attr} style="display:flex;align-items:flex-start;gap:10px;'
            f'padding:8px 0;border-bottom:1px solid #fef3c7">'
            f'<span style="font-size:14px;flex-shrink:0;margin-top:2px">{_ai_icon}</span>'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:13px;font-weight:500;color:#111;line-height:1.35">{he(_ai_disp_lbl)}</div>'
            + (f'<div style="font-size:11px;color:{_ai_urg_clr};margin-top:1px">{he(_ai_why)}</div>' if _ai_why else '')
            + _ai_btns
            + '</div></div>'
        )
    if _avail_attn_html:
        _avail_attn_html = (
            f'<div id="avail-attn-section" style="margin-bottom:10px">{_avail_attn_html}</div>'
        )

    # ── RIGHT RAIL: Available Benefits ──────────────────────────────────────
    # Build the right-rail panel: urgent action items at top, then all
    # actionable benefit assets (certificates, travel/cash credits) sorted
    # by score.  This replaces the old top_benefits_html centre section.
    _rail_html = ""

    # 1. Urgent / login-required action items (compact, with dismiss)
    _rail_attn_html = ""
    for _rai in _all_action_items[:4]:
        _rai_id      = _rai.get("id")
        _rai_urgency = _rai.get("urgency", "info")
        _rai_label   = _rai.get("label", "")
        _rai_val     = _rai.get("value", "")
        _rai_btype   = _rai.get("btype", "")
        _rai_days    = _rai.get("days_left")
        _rai_disp    = _rai_val if not _rai_label or _rai_label == "Login required" else f"{_rai_label}: {_rai_val}"
        if _rai_btype == "login_required":
            _rai_why = "Re-authenticate to continue syncing"
        elif _rai_days is not None and _rai_days < 0:
            _rai_why = "Expired"
        else:
            _rai_why = _why_shown(_rai_btype, _rai_days)
        _rai_urg_clr = "#dc2626" if _rai_urgency == "urgent" else "#d97706"
        _rai_icon    = "🔴" if _rai_urgency == "urgent" else "🟡"
        # Dismiss button (only for persisted items)
        _rai_dismiss = ""
        if _rai_id is not None:
            _rai_rm_js = (
                f"document.getElementById('rail-ai-{_rai_id}').remove();"
                f"if(!document.querySelector('[id^=\"rail-ai-\"]'))document.getElementById('rail-attn-wrap').remove();"
            )
            _rai_dismiss_js = (
                f"fetch('/api/action-items/{_rai_id}/dismiss',{{"
                f"method:'POST',headers:{{'X-CSRF-Token':'{get_csrf_token()}','Content-Type':'application/json'}},"
                f"credentials:'same-origin'}}).then(r=>r.ok&&({_rai_rm_js}))"
            )
            _rai_dismiss = (
                f'<button onclick="{_rai_dismiss_js}" style="font-size:10px;color:#9ca3af;'
                f'background:none;border:none;padding:0;cursor:pointer;margin-left:auto;flex-shrink:0">✕</button>'
            )
        _rai_id_attr = f' id="rail-ai-{_rai_id}"' if _rai_id is not None else ""
        _rail_attn_html += (
            f'<div{_rai_id_attr} style="display:flex;align-items:flex-start;gap:8px;'
            f'padding:7px 0;border-bottom:1px solid #e0dbd4">'
            f'<span style="font-size:13px;flex-shrink:0;margin-top:1px">{_rai_icon}</span>'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:12px;font-weight:500;color:#111;line-height:1.35;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{he(_rai_disp)}</div>'
            + (f'<div style="font-size:10.5px;color:{_rai_urg_clr};margin-top:1px">{he(_rai_why)}</div>' if _rai_why else '')
            + f'</div>{_rai_dismiss}</div>'
        )
    if _rail_attn_html:
        _rail_html += f'<div id="rail-attn-wrap" style="margin-bottom:12px">{_rail_attn_html}</div>'

    # 2. Benefit assets: certificates first, then travel/cash credits
    # Pull from scored hero_candidates which are already sorted by score.
    # Show up to 8 items so the rail is always rich on wide monitors.
    _rail_assets_html = ""
    import json as _json_rail
    for _rpr, _, _rhdisp, _rhlbl, _rhval, _rhexp, _rhbtype in _hero_candidates[:8]:
        _ricon = _hero_icon(_rhlbl.lower())
        # Color: blue for certs, green for credits
        if _rhbtype == "certificate":
            _rlbl_color = "#1d4ed8"
        elif _rhbtype in ("travel_credit", "cash_credit"):
            _rlbl_color = "#059669"
        else:
            _rlbl_color = "#374151"
        # Skip-val filter
        _skip_vals_r = {'available','active','yes','enabled','valid','earned',''}
        _rshow_val   = _rhval if _rhval.lower().strip() not in _skip_vals_r else ""
        # Expiry sub-line
        _rsub_parts = [f'<span style="color:#9ca3af;font-size:10.5px">{he(_rhdisp)}</span>']
        if _rhexp is not None and _rhexp >= 0:
            import datetime as _rdt
            if _rhexp <= 30:
                _rsub_parts.append(f'<span style="color:#dc2626;font-weight:600;font-size:10px">Exp {_rhexp}d</span>')
            else:
                _rexp_d = _rdt.date.today() + _rdt.timedelta(days=_rhexp)
                _rsub_parts.append(f'<span style="color:#d97706;font-size:10px">Exp {_rexp_d.strftime("%b %Y")}</span>')
        _rsub_html = ' · '.join(_rsub_parts)
        # Why-shown copy (now correct because _rhbtype comes from the tuple)
        _rwhy = _why_shown(_rhbtype, _rhexp, _rhdisp)
        # Drawer data
        _rfk = f"{_rhdisp}::{_rhlbl}"
        _rbtype_draw = _dash_corrections.get(_rfk, _rhbtype)
        _rbd_data = _json_rail.dumps({
            "label": _rhlbl, "account": _rhdisp,
            "value": _rhval, "icon": _ricon, "expDays": _rhexp,
            "field_key": _rfk, "btype": _rbtype_draw,
            "corrected": _rfk in _dash_corrections
        }).replace("'", "&#39;")
        _rail_assets_html += (
            f'<div style="display:flex;gap:9px;padding:8px 4px;cursor:pointer;'
            f'border-radius:7px;transition:background 0.1s" '
            f'onmouseover="this.style.background=\'#e8e2db\'" '
            f'onmouseout="this.style.background=\'\'" '
            f'onclick="openBenefitDrawer(this)" data-benefit=\'{_rbd_data}\'>'
            f'<span style="font-size:17px;flex-shrink:0;margin-top:1px;line-height:1.3">{_ricon}</span>'
            f'<div style="flex:1;min-width:0">'
            f'<div style="font-size:13px;font-weight:600;color:{_rlbl_color};line-height:1.3;'
            f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{he(_rhlbl)}'
            + (f'<span style="font-size:11.5px;font-weight:400;color:#6b7280;margin-left:5px">{he(_rshow_val)}</span>' if _rshow_val else '')
            + f'</div>'
            f'<div style="margin-top:2px;line-height:1.4">{_rsub_html}</div>'
            + (f'<div style="font-size:10.5px;color:#6b7280;margin-top:1px">{he(_rwhy)}</div>' if _rwhy else '')
            + f'</div></div>'
        )

    if _rail_assets_html:
        _rail_html += (
            '<h2 style="font-size:10px;font-weight:700;color:#8b7d6b;margin:0 0 8px;'
            'text-transform:uppercase;letter-spacing:.07em">Available</h2>'
            + _rail_assets_html
        )
    elif _account_count > 0:
        _rail_html += (
            '<div style="font-size:12px;color:#9ca3af;margin-top:8px">✓ Nothing to redeem right now</div>'
        )

    if _rail_html:
        available_rail_html = _rail_html
    else:
        available_rail_html = ""

    # Keep stub for any legacy references
    top_benefits_html = ""

    opportunities_html = """
<style>
@keyframes opp-shimmer{0%{background-position:-400px 0}100%{background-position:400px 0}}
.opp-skeleton{background:linear-gradient(90deg,#f0ece8 25%,#e8e4e0 50%,#f0ece8 75%);
  background-size:400px 100%;animation:opp-shimmer 1.4s infinite;border-radius:4px}
</style>
<div id="opportunities-section" style="margin-bottom:28px">
  <h2 style="font-size:14px;font-weight:700;color:#111;margin:0 0 12px;
     text-transform:uppercase;letter-spacing:.05em">Opportunities</h2>
  <div id="opportunities-panel">
    <div class="opp-skeleton" style="height:70px;margin-bottom:10px"></div>
    <div class="opp-skeleton" style="height:70px;margin-bottom:10px"></div>
  </div>
</div>
<script>
(function(){
  function escO(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function loadOpportunities(retrying){
    fetch('/api/intent/recent').then(function(r){return r.json();}).then(function(intents){
      var ctx = (intents && intents.length) ? intents[0].intent_type : null;
      var url = '/api/opportunities' + (ctx ? '?context=' + ctx : '');
      return fetch(url).then(function(r){return r.json();});
    }).then(function(data){
      var section = document.getElementById('opportunities-section');
      var panel   = document.getElementById('opportunities-panel');
      if(!data || !data.opportunities || !data.opportunities.length){
        // Post-sync: give accounts extra time to finish writing, then retry once
        if(!retrying && sessionStorage.getItem('mighty-opp-retry')){
          sessionStorage.removeItem('mighty-opp-retry');
          setTimeout(function(){ loadOpportunities(true); }, 4000);
          return;
        }
        section.style.display='none'; return;
      }
      sessionStorage.removeItem('mighty-opp-retry');
      var html = '';
      data.opportunities.forEach(function(opp){
        var urgStyle = opp.urgency==='urgent' ? 'border-left:3px solid #dc2626' :
                       opp.urgency==='soon'   ? 'border-left:3px solid #f59e0b' :
                                                'border-left:3px solid #e5e7eb';
        var rows = opp.components.slice(0,4).map(function(c){
          var txBadge = c.is_transfer ? '<span style="font-size:9px;color:#6366f1;margin-left:4px;'
            +'font-weight:600;letter-spacing:.04em">TRANSFER</span>' : '';
          var expBadge = c.exp_label ? '<span style="font-size:10px;color:#dc2626;margin-left:6px">'
            +escO(c.exp_label)+'</span>' : '';
          return '<div style="display:flex;align-items:baseline;justify-content:space-between;'
            +'padding:3px 0;font-size:12px">'
            +'<span style="color:#374151">'+escO(c.label)+txBadge+'</span>'
            +'<span style="color:#6b7280">'+escO(c.value)+expBadge+'</span></div>';
        }).join('');
        var whyHtml = '<div style="margin-top:6px;font-size:11px;color:#9ca3af;font-style:italic">'
          +escO(opp.why)+'</div>';
        html += '<div style="padding:12px;background:#fff;border:1px solid #e5e7eb;border-radius:8px;'
          +'margin-bottom:10px;'+urgStyle+'">'
          +'<div style="font-size:13px;font-weight:600;color:#111;margin-bottom:6px">'+escO(opp.title)+'</div>'
          +rows+whyHtml
          +'</div>';
      });
      panel.innerHTML=html;
    }).catch(function(){ document.getElementById('opportunities-section').style.display='none'; });
  }
  // After a Sync All, wait 5s for the server to finish committing all account data before querying
  var postSync = sessionStorage.getItem('mighty-post-sync');
  if(postSync){
    sessionStorage.removeItem('mighty-post-sync');
    sessionStorage.setItem('mighty-opp-retry','1');  // enable one extra retry if still empty
    setTimeout(loadOpportunities, 5000);
  } else { loadOpportunities(); }
})();
</script>
"""

    # ── Wallet Insights: card descriptions relevant to connected accounts ─────
    # wallet_insights_html suppressed — card recommendations removed per product direction
    # (surfacing existing benefits, not recommending new cards)
    wallet_insights_html = ""
    _wallet_insights_unused = """
<div id="wallet-insights-wrap" style="margin-bottom:28px;display:none">
  <button id="wallet-insights-toggle"
    onclick="toggleWalletInsights()"
    style="display:flex;align-items:center;gap:6px;background:none;border:none;
           cursor:pointer;padding:0;font-family:inherit">
    <h2 style="font-size:14px;font-weight:700;color:#111;margin:0;
               text-transform:uppercase;letter-spacing:.05em">&#10022; Worth Knowing About</h2>
    <span id="wallet-insights-count"
      style="font-size:11px;background:#f3f4f6;color:#6b7280;border-radius:10px;
             padding:1px 7px;font-weight:500"></span>
    <span id="wallet-insights-chevron"
      style="font-size:11px;color:#9ca3af;margin-left:2px">&#9660;</span>
  </button>
  <div id="wallet-insights-body" style="display:none;margin-top:12px">
    <!-- Populated by JS -->
  </div>
</div>
<script>
(function() {
  var _wiOpen = false;
  window.toggleWalletInsights = function() {
    _wiOpen = !_wiOpen;
    document.getElementById('wallet-insights-body').style.display = _wiOpen ? 'block' : 'none';
    document.getElementById('wallet-insights-chevron').innerHTML = _wiOpen ? '&#9650;' : '&#9660;';
  };

  function esc(s) {
    return String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function buildWalletInsights(recs) {
    if (!recs.length) return;
    var wrap = document.getElementById('wallet-insights-wrap');
    var body = document.getElementById('wallet-insights-body');
    var countEl = document.getElementById('wallet-insights-count');
    if (!wrap || !body) return;

    countEl.textContent = recs.length;
    var html = '';
    recs.forEach(function(rec) {
      // Build drawer data for this card recommendation
      var drawerData = JSON.stringify({
        icon: '💳',
        label: rec.card_name,
        account: rec.issuer + ' · for ' + esc(rec.trigger_source || '').replace(/_/g,' ') + ' members',
        card_benefits: rec.benefits || [],
        card_issuer: rec.issuer,
        trigger_account: (rec.trigger_source || '').replace(/_/g,' '),
        last_verified: rec.last_verified || '',
        source_url: rec.source_url || '',
        confidence: rec.confidence || 'high',
      });
      html += '<div style="background:#fff;border:1px solid #e5e7eb;border-radius:10px;'
            + 'padding:14px 16px;margin-bottom:10px">';
      html += '<div style="display:flex;align-items:center;justify-content:space-between;'
            + 'gap:8px;margin-bottom:8px">';
      html += '<div>';
      html += '<div style="font-size:13px;font-weight:600;color:#111">' + esc(rec.card_name) + '</div>';
      html += '<div style="font-size:11px;color:#9ca3af">' + esc(rec.issuer) + '</div>';
      html += '</div>';
      html += '<button onclick=\'openBenefitDrawer(this)\' data-benefit=\'' + drawerData.replace(/'/g,"&#39;") + '\' '
            + 'style="font-size:11px;color:#6366f1;background:none;border:none;cursor:pointer;'
            + 'padding:0;font-family:inherit;white-space:nowrap">Details ›</button>';
      html += '</div>';
      html += '<ul style="margin:0;padding:0 0 0 14px;list-style:disc">';
      (rec.benefits || []).slice(0, 3).forEach(function(b) {
        html += '<li style="font-size:12px;color:#374151;margin-bottom:3px;line-height:1.45">'
              + esc(b) + '</li>';
      });
      if ((rec.benefits || []).length > 3) {
        html += '<li style="font-size:12px;color:#9ca3af;list-style:none;margin-top:2px">'
              + '+ ' + ((rec.benefits||[]).length - 3) + ' more — click Details</li>';
      }
      html += '</ul>';
      if (rec.last_verified) {
        html += '<div style="font-size:10px;color:#d1d5db;margin-top:8px">'
              + 'Published benefit · verified ' + esc(rec.last_verified) + '</div>';
      }
      html += '</div>';
    });

    body.innerHTML = html;
    wrap.style.display = 'block';
  }

  // Check if a contextual intent was recently fired by the extension
  var recentContext = null;
  try { recentContext = sessionStorage.getItem('mighty_last_context'); } catch(e) {}

  var url = '/api/benefits/discover';
  if (recentContext) url += '?context=' + encodeURIComponent(recentContext);

  fetch(url)
    .then(function(r) { return r.ok ? r.json() : null; })
    .then(function(d) { if (d && d.recommendations) buildWalletInsights(d.recommendations); })
    .catch(function() {});
})();
</script>
"""

    # ── RELEVANT NOW — cross-domain intent recommendations ──────────────────
    _rn_items = _relevant_now_items(session["user_id"])
    if _rn_items:
        import json as _json_rn_dash
        _rn_intent = _rn_items[0]["intent_type"]
        _rn_intent_labels = {
            "flight": "searching for flights",
            "hotel":  "searching for hotels",
            "car":    "searching for rental cars",
            "shopping": "shopping online",
            "dining": "searching for restaurants",
            "credit_card": "looking at credit card offers",
        }
        _rn_intent_label = _rn_intent_labels.get(_rn_intent, f"searching for {_rn_intent}")
        _rn_adjacent_labels = {
            "flight": "hotels & car rentals",
            "hotel":  "flights & car rentals",
            "car":    "flights & hotels",
            "shopping": "dining",
            "dining": "shopping",
            "credit_card": "travel & cash back rewards",
        }
        _rn_adj_label = _rn_adjacent_labels.get(_rn_intent, "related benefits")
        _rn_type_icons = {
            "certificate":   "🎫",
            "travel_credit": "✈",
            "cash_credit":   "💳",
            "elite_status":  "◆",
            "membership":    "🔑",
        }

        _rn_rows_html = ""
        for _rni in _rn_items:
            _rni_icon  = _rn_type_icons.get(_rni["btype"], "•")
            _rni_why   = _why_shown(_rni["btype"], _rni.get("days_left"), _rni["source"])
            _rni_exp   = _rni.get("days_left")
            # Drawer data
            _rni_bd = _json_rn_dash.dumps({
                "label":    _rni["label"],
                "account":  _rni["source"],
                "value":    _rni["value"],
                "icon":     _rni_icon,
                "expDays":  _rni_exp,
                "field_key": _rni["field_key"],
                "btype":    _rni["btype"],
            }).replace("'", "&#39;")
            _rni_exp_html = ""
            if _rni_exp is not None and 0 <= _rni_exp <= 60:
                _exp_col = "#dc2626" if _rni_exp <= 14 else "#d97706"
                _rni_exp_html = (
                    f'<span style="font-size:10px;color:{_exp_col};margin-left:4px;'
                    f'font-weight:600">· {_rni_exp}d left</span>'
                )
            _rn_rows_html += (
                f'<div style="display:flex;align-items:flex-start;gap:10px;padding:8px 4px;'
                f'cursor:pointer;border-radius:7px;transition:background 0.1s" '
                f'onmouseover="this.style.background=\'#f0f9ff\'" '
                f'onmouseout="this.style.background=\'\'" '
                f'onclick="openBenefitDrawer(this)" data-benefit=\'{_rni_bd}\'>'
                f'<span style="font-size:17px;flex-shrink:0;margin-top:1px;line-height:1.3">{_rni_icon}</span>'
                f'<div style="flex:1;min-width:0">'
                f'<div style="font-size:13.5px;font-weight:600;color:#0c4a6e;line-height:1.3;'
                f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis">'
                f'{he(_rni["label"])}{_rni_exp_html}</div>'
                f'<div style="font-size:11px;color:#6b7280;margin-top:1px">{he(_rni["source"])}</div>'
                + (f'<div style="font-size:10.5px;color:#0369a1;margin-top:2px">{he(_rni_why)}</div>' if _rni_why else '')
                + f'</div></div>'
            )

        relevant_now_html = (
            f'<div style="margin-bottom:28px;background:#f0f9ff;border:1px solid #bae6fd;'
            f'border-radius:12px;padding:16px 18px">'
            f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:12px">'
            f'<span style="font-size:13px;font-weight:700;color:#0369a1;text-transform:uppercase;'
            f'letter-spacing:.05em">Relevant now</span>'
            f'<span style="font-size:11px;background:#e0f2fe;color:#0284c7;padding:2px 8px;'
            f'border-radius:99px;font-weight:500">You\'re {he(_rn_intent_label)}</span>'
            f'</div>'
            f'<div style="font-size:11px;color:#6b7280;margin-bottom:10px">'
            f'Your {he(_rn_adj_label)} benefits — useful for your current trip.</div>'
            f'{_rn_rows_html}'
            f'</div>'
        )
    else:
        relevant_now_html = ""
    # ── END RELEVANT NOW ─────────────────────────────────────────────────────

    # Build re-auth banner if any accounts need re-login
    if login_required_accounts:
        _n = len(login_required_accounts)
        _names_html = ", ".join(f"<strong>{he(n)}</strong>" for n in login_required_accounts)
        _acct_word = "account" if _n == 1 else "accounts"
        reauth_banner = (
            f'<div style="margin:12px 24px 0;background:#fef2f2;border:1px solid rgba(239,68,68,0.25);'
            f'border-radius:10px;padding:11px 14px;display:flex;align-items:center;gap:10px">'
            f'<span style="font-size:15px">🔐</span>'
            f'<span style="flex:1;font-size:12.5px;color:#991b1b;line-height:1.45">'
            f'<strong>{_n} {_acct_word} need re-authentication:</strong> {_names_html} — '
            f'log in to each site in Chrome, then click ↻ to re-sync.</span>'
            f'<button onclick="this.closest(\'div\').remove()" style="background:none;border:none;'
            f'cursor:pointer;color:#ef4444;font-size:16px;line-height:1;padding:0 2px" title="Dismiss">×</button>'
            f'</div>'
        )
    else:
        reauth_banner = ""

    # Build "new account detected" banner for recently auto-captured custom accounts (last 10 min)
    _ten_min_ago = (datetime.utcnow() - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%S")
    _new_custom = db.execute(
        "SELECT source, synced_at FROM account_data WHERE user_id=? AND source LIKE 'custom_%' "
        "AND synced_at >= ? ORDER BY synced_at DESC",
        (user["id"], _ten_min_ago)
    ).fetchall()
    if _new_custom:
        _new_names = []
        for _nc in _new_custom:
            # Try to get display name from credentials extra_enc
            _nc_cred = db.execute(
                "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
                (user["id"], _nc["source"])
            ).fetchone()
            _nc_name = _nc["source"].replace("custom_", "").replace("_", " ").title()
            if _nc_cred and _nc_cred["extra_enc"]:
                try:
                    _nc_ex = json.loads(decrypt_cred(user["id"], _nc_cred["extra_enc"]))
                    _nc_name = _nc_ex.get("display_name") or _nc_name
                except Exception:
                    pass
            _new_names.append(_nc_name)
        _n_new = len(_new_names)
        _new_word = "account" if _n_new == 1 else "accounts"
        _new_names_html = ", ".join(f"<strong>{he(n)}</strong>" for n in _new_names)
        new_accounts_banner = (
            f'<div style="margin:12px 24px 0;background:#f0fdf4;border:1px solid rgba(5,150,105,0.25);'
            f'border-radius:10px;padding:11px 14px;display:flex;align-items:center;gap:10px">'
            f'<span style="font-size:15px">✨</span>'
            f'<span style="flex:1;font-size:12.5px;color:#065f46;line-height:1.45">'
            f'<strong>Mighty just captured {_n_new} new {_new_word}:</strong> {_new_names_html} — '
            f'data is being extracted and will appear on your dashboard shortly.</span>'
            f'<button onclick="this.closest(\'div\').remove()" style="background:none;border:none;'
            f'cursor:pointer;color:#059669;font-size:16px;line-height:1;padding:0 2px" title="Dismiss">×</button>'
            f'</div>'
        )
    else:
        new_accounts_banner = ""

    onboarding_modal = ""
    if not user["onboarded"]:
        onboarding_modal = """
<div id="onboarding-overlay" style="position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:1000;display:flex;align-items:center;justify-content:center;padding:20px">
  <div style="background:#fff;border-radius:16px;max-width:480px;width:100%;padding:28px 28px 24px;box-shadow:0 20px 60px rgba(0,0,0,0.3)">
    <div style="font-size:28px;text-align:center;margin-bottom:12px">&#128272;</div>
    <h2 style="font-size:20px;font-weight:700;text-align:center;color:#111;margin:0 0 8px">How Mighty works</h2>
    <p style="font-size:14px;color:#4b5563;text-align:center;margin:0 0 20px;line-height:1.6">
      Mighty reads your connected account pages and extracts only the facts you care about &mdash; balances, expiry dates, due dates, and credits.
      <strong>Raw page text is never shared</strong> and is discarded after extraction.
    </p>
    <div style="background:#f9fafb;border-radius:10px;padding:14px 16px;margin-bottom:20px">
      <div style="font-size:13px;color:#374151;display:flex;flex-direction:column;gap:8px">
        <div>&#9989; <strong>What we extract:</strong> Account balances, expiry dates, payment due dates, credits</div>
        <div>&#128683; <strong>What we don&#39;t store:</strong> Full browsing history, passwords, or raw page content (unless you disable deletion)</div>
        <div>&#128274; <strong>Your data:</strong> Encrypted at rest, never sold or shared</div>
      </div>
    </div>
    <button onclick="dismissOnboarding()" style="width:100%;padding:12px;background:#111;color:#fff;border:none;border-radius:8px;font-size:14px;font-weight:600;cursor:pointer">
      Got it &mdash; take me to my dashboard
    </button>
  </div>
</div>
<script>
function dismissOnboarding() {
  document.getElementById('onboarding-overlay').style.display = 'none';
  var csrf = (document.querySelector('input[name="_csrf"]') || {}).value || '';
  fetch('/api/onboarding/complete', {
    method: 'POST',
    headers: {'X-CSRF-Token': csrf, 'Content-Type': 'application/json'},
    credentials: 'same-origin'
  }).catch(function(){});
}
</script>"""

    _csrf = get_csrf_token()
    return (DASHBOARD_HTML
            .replace("{_SIDEBAR_}",               _sidebar_html('dashboard', user["email"], _csrf))
            .replace("{email}",                   he(user["email"]))
            .replace("{feed_html}",               feed)
            .replace("{pending_count}",           str(pending_count))
            .replace("{pending_display}",         pending_display)
            .replace("{expiring_count}",          str(total_expiring))
            .replace("{expiring_plural}",         "s" if total_expiring != 1 else "")
            .replace("{expiring_display}",        "flex" if total_expiring > 0 else "none")
            .replace("{agent_status_indicator}",  agent_status_indicator)
            .replace("{agent_cta_button}",        agent_cta_button)
            .replace("{feed_col_hidden}",         feed_col_hidden)
            .replace("{welcome_state}",           welcome_state)
            .replace("{onboarding_banner}",       onboarding_banner)
            .replace("{reauth_banner}",           reauth_banner)
            .replace("{new_accounts_banner}",     new_accounts_banner)
            .replace("{account_data_html}",       account_data_html)
            .replace("{hero_section_html}",       hero_section_html)
            .replace("{insights_html}",           insights_html)
            .replace("{available_rail_html}",     available_rail_html)
            .replace("{action_center_html}",      action_center_html)
            .replace("{recently_found_html}",     recently_found_html)
            .replace("{relevant_now_html}",       relevant_now_html)
            .replace("{wallet_insights_html}",    wallet_insights_html)
            .replace("{value_center_html}",       value_center_html)
            .replace("{top_benefits_html}",       top_benefits_html)
            .replace("{progress_section_html}",   progress_section_html)
            .replace("{opportunities_html}",      "")
            .replace("{onboarding_modal}",        onboarding_modal)
            .replace("{dash_modals}",             _build_dash_modals(configured, _csrf))
            .replace("{csrf_token}",              _csrf)
            .replace("{global_sync_label}",       _global_sync_label))

@app.route("/settings")
@require_login
def settings():
    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not user:
        session.clear()
        return redirect("/login")
    topic = ntfy_topic(user["api_key"])
    postmark_ok = bool(POSTMARK_API_KEY)
    # postmark_warn: initially show warning only if email notifs are ON but Postmark not configured
    postmark_warn = "block" if (user["notify_email"] and not postmark_ok) else "none"
    raw_key = user["api_key"]
    # Mask: show prefix (up to 3 chars) + bullets
    key_prefix = raw_key[:3] if raw_key else ""
    api_key_masked = key_prefix + "•" * max(0, len(raw_key) - 3)
    _csrf = get_csrf_token()
    notif_pref = user["notification_pref"] if user["notification_pref"] else "quiet"
    return (SETTINGS_HTML
            .replace("{_SIDEBAR_}",               _sidebar_html('settings', user["email"], _csrf))
            .replace("{email}",                   he(user["email"]))
            .replace("{api_key}",                 raw_key)
            .replace("{api_key_masked}",          he(api_key_masked))
            .replace("{ntfy_topic}",              topic)
            .replace("{push_checked}",            "checked" if user["notify_push"]    else "")
            .replace("{ntfy_checked}",            "checked" if user["notify_ntfy"]    else "")
            .replace("{email_checked}",           "checked" if user["notify_email"]   else "")
            .replace("{alert_expiry_checked}",    "checked" if user["alert_expiry_emails"] else "")
            .replace("{minimal_logging_checked}", "checked" if user["minimal_logging"] else "")
            .replace("{delete_raw_checked}",      "checked" if user["delete_raw_after_extract"] else "")
            .replace("{postmark_warn}",           postmark_warn)
            .replace("{postmark_js}",             "true" if postmark_ok else "false")
            .replace("{notification_pref}",       notif_pref)
            .replace("{preferred_name}",          he(user["preferred_name"] or ""))
            .replace("{csrf_token}",              _csrf))

@app.route("/extension-setup")
@require_login
def extension_setup():
    """Page the Chrome extension reads to auto-configure its API key.
    Contains the key in a machine-readable meta tag so the extension can
    extract it without user copy-paste.
    """
    user = get_db().execute("SELECT api_key FROM users WHERE id=?", (session["user_id"],)).fetchone()
    key  = user["api_key"] if user else ""
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<meta name="mighty-api-key" content="{he(key)}">
<title>Mighty Extension Setup</title>
<style>
  body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
    display:flex;flex-direction:column;align-items:center;justify-content:center;
    min-height:100vh;margin:0;background:#f0fdf4;color:#1c1917}}
  .card{{background:#fff;border:1px solid #bbf7d0;border-radius:16px;
    padding:40px 48px;text-align:center;max-width:440px;
    box-shadow:0 4px 24px rgba(5,150,105,0.1)}}
  .icon{{font-size:48px;margin-bottom:16px}}
  h1{{font-size:20px;font-weight:700;margin:0 0 8px;color:#065f46}}
  p{{font-size:14px;color:#6b7280;line-height:1.6;margin:0 0 24px}}
  .status{{font-size:13px;font-weight:600;padding:10px 16px;border-radius:8px;
    background:#f0fdf4;border:1px solid #bbf7d0;color:#059669}}
  .spinner{{display:inline-block;width:14px;height:14px;border:2px solid #bbf7d0;
    border-top-color:#059669;border-radius:50%;animation:spin 0.7s linear infinite;
    margin-right:8px;vertical-align:middle}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
</style>
</head><body>
<div class="card">
  <div class="icon">🔌</div>
  <h1>Connecting Mighty Extension</h1>
  <p>The Mighty Chrome extension is reading this page to configure itself automatically.
     You can close this tab once it confirms.</p>
  <div class="status" id="status">
    <span class="spinner"></span> Waiting for extension…
  </div>
</div>
<script>
  // Extension sets mighty_setup_done in sessionStorage when it reads the key
  const check = setInterval(() => {{
    if (sessionStorage.getItem('mighty_setup_done')) {{
      clearInterval(check);
      document.getElementById('status').innerHTML = '✓ Extension configured successfully — you can close this tab';
      document.getElementById('status').style.background = '#f0fdf4';
    }}
  }}, 500);
  // Fallback: show success after 3s (extension may have already read and navigated away)
  setTimeout(() => {{
    if (!sessionStorage.getItem('mighty_setup_done'))
      document.getElementById('status').innerHTML = '✓ Done — the extension should be configured now';
  }}, 3000);
</script>
</body></html>""", 200, {"Content-Type": "text/html"}


@app.route("/settings/export-csv")
@require_login
def export_csv():
    db      = get_db()
    user_id = session["user_id"]
    rows    = db.execute(
        "SELECT created_at, action_type, label, fields, status, outcome, decided_at "
        "FROM actions WHERE user_id=? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    si = io.StringIO()
    writer = csv.writer(si)
    writer.writerow(["Date", "Action Type", "Description", "Details", "Status", "Outcome", "Decided At"])
    for row in rows:
        try:
            fields_raw = json.loads(row["fields"]) if row["fields"] else []
            if isinstance(fields_raw, list):
                details = "; ".join(
                    "{}: {}".format(pair[0], pair[1])
                    for pair in fields_raw
                    if isinstance(pair, (list, tuple)) and len(pair) >= 2
                )
            elif isinstance(fields_raw, dict):
                details = "; ".join("{}: {}".format(k, v) for k, v in fields_raw.items())
            else:
                details = ""
        except Exception:
            details = ""
        writer.writerow([
            row["created_at"],
            row["action_type"],
            row["label"],
            details,
            row["status"],
            row["outcome"] or "",
            row["decided_at"] or "",
        ])
    output = si.getvalue()
    filename = "mighty-activity-{}.csv".format(datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    resp = make_response(output)
    resp.headers["Content-Type"] = "text/csv"
    resp.headers["Content-Disposition"] = "attachment; filename={}".format(filename)
    return resp

@app.route("/settings/delete-activity", methods=["POST"])
@require_login
def delete_activity():
    check_csrf()
    db = get_db()
    db.execute("DELETE FROM actions WHERE user_id=?", (session["user_id"],))
    db.commit()
    return jsonify({"ok": True})

@app.route("/settings/change-password", methods=["POST"])
@require_login
def change_password():
    check_csrf()
    data     = request.get_json(force=True, silent=True) or {}
    current  = data.get("current", "")
    new_pw   = data.get("password", "")
    db       = get_db()
    user     = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not user or not check_pw(user["password_hash"], current):
        return jsonify({"error": "Incorrect current password."}), 403
    if len(new_pw) < 6:
        return jsonify({"error": "New password must be at least 6 characters."}), 400
    db.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_pw(new_pw), session["user_id"]))
    db.commit()
    return jsonify({"ok": True})

@app.route("/settings/name", methods=["POST"])
@require_login
def save_preferred_name():
    check_csrf()
    name = (request.form.get("preferred_name") or "").strip()[:40]
    get_db().execute("UPDATE users SET preferred_name=? WHERE id=?", (name or None, session["user_id"]))
    get_db().commit()
    return jsonify({"ok": True})


@app.route("/settings/change-email", methods=["POST"])
@require_login
def change_email():
    check_csrf()
    data     = request.get_json(force=True, silent=True) or {}
    new_email = data.get("email", "").strip().lower()
    password  = data.get("password", "")
    if not new_email or "@" not in new_email:
        return jsonify({"error": "Please enter a valid email address."}), 400
    db   = get_db()
    user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not user or not check_pw(user["password_hash"], password):
        return jsonify({"error": "Incorrect password."}), 403
    if db.execute("SELECT 1 FROM users WHERE email=? AND id!=?", (new_email, session["user_id"])).fetchone():
        return jsonify({"error": "That email address is already in use."}), 409
    db.execute("UPDATE users SET email=? WHERE id=?", (new_email, session["user_id"]))
    db.commit()
    session["email"] = new_email
    return jsonify({"ok": True})

@app.route("/settings/delete-account", methods=["POST"])
@require_login
def delete_account():
    check_csrf()
    data     = request.get_json(force=True, silent=True) or {}
    password = data.get("password", "")
    db       = get_db()
    user_id  = session["user_id"]
    user     = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    if not user or not check_pw(user["password_hash"], password):
        return jsonify({"error": "Incorrect password."}), 403
    db.execute("DELETE FROM push_subscriptions WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM actions WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM password_resets WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM account_credentials WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM account_data WHERE user_id=?", (user_id,))
    # Orphan cleanup — tables added after the original delete_account was written
    db.execute("DELETE FROM field_history WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM field_observations WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM approved_domains WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM privacy_audit_log WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM field_candidates WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM reminder_snoozes WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM intent_history WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM benefit_feedback WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM notifications_sent WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM archived_benefits WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM action_items WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM benefit_corrections WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    session.clear()
    return jsonify({"ok": True})

@app.route("/download/mighty_mcp.py")
@require_login
def download_mcp():
    user = get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    api_key  = user["api_key"]
    base     = base_url()
    # Read the MCP server from disk if available, otherwise return a minimal version
    script_path = os.path.join(os.path.dirname(__file__), "mighty_mcp.py")
    if os.path.exists(script_path):
        with open(script_path) as f:
            script = f.read()
        # Inject credentials as defaults
        script = script.replace(
            'os.environ.get("MIGHTY_API_KEY", "")',
            f'os.environ.get("MIGHTY_API_KEY", "{api_key}")'
        ).replace(
            'os.environ.get("MIGHTY_BASE_URL", "")',
            f'os.environ.get("MIGHTY_BASE_URL", "{base}")'
        )
    else:
        script = f'# Mighty MCP Server\n# API Key: {api_key}\n# Visit {base} for setup instructions\n'
    resp = make_response(script)
    resp.headers["Content-Disposition"] = "attachment; filename=mighty_mcp.py"
    resp.headers["Content-Type"] = "text/x-python"
    return resp

@app.route("/dashboard/decide/<action_id>", methods=["POST"])
@require_login
def decide(action_id):
    data     = request.get_json(force=True)
    decision = data.get("decision")
    if decision not in ("approve", "deny"):
        return jsonify({"error": "invalid"}), 400
    status = "approved" if decision == "approve" else "denied"
    db = get_db()
    db.execute(
        "UPDATE actions SET status=?, decided_at=? WHERE id=? AND user_id=? AND status='pending'",
        (status, iso(), action_id, session["user_id"]),
    )
    db.commit()
    return jsonify({"status": status})

@app.route("/dashboard/has-pending")
@require_login
def has_pending():
    expire_pending()
    since = request.args.get("since")
    if since:
        try:
            since_dt = datetime.fromtimestamp(float(since), tz=timezone.utc).isoformat()
            row = get_db().execute(
                "SELECT 1 FROM actions WHERE user_id=? AND status='pending' AND created_at > ? LIMIT 1",
                (session["user_id"], since_dt),
            ).fetchone()
        except (ValueError, OSError):
            row = get_db().execute(
                "SELECT 1 FROM actions WHERE user_id=? AND status='pending' LIMIT 1",
                (session["user_id"],),
            ).fetchone()
    else:
        row = get_db().execute(
            "SELECT 1 FROM actions WHERE user_id=? AND status='pending' LIMIT 1",
            (session["user_id"],),
        ).fetchone()
    return jsonify({"pending": bool(row)})

@app.route("/dashboard/notifications", methods=["POST"])
@require_login
def update_notifications():
    check_csrf()
    data = request.get_json(force=True)
    db = get_db()
    db.execute(
        "UPDATE users SET notify_email=?, notify_ntfy=?, notify_push=? WHERE id=?",
        (1 if data.get("email") else 0,
         1 if data.get("ntfy") else 0,
         1 if data.get("push") else 0,
         session["user_id"])
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/settings/alert-expiry", methods=["POST"])
@require_login
def update_alert_expiry():
    check_csrf()
    data = request.get_json(force=True)
    get_db().execute(
        "UPDATE users SET alert_expiry_emails=? WHERE id=?",
        (1 if data.get("enabled") else 0, session["user_id"])
    )
    get_db().commit()
    return jsonify({"ok": True})


@app.route("/settings/privacy", methods=["POST"])
@require_login
def update_privacy():
    check_csrf()
    data = request.get_json(force=True)
    get_db().execute(
        "UPDATE users SET minimal_logging=?, delete_raw_after_extract=? WHERE id=?",
        (
            1 if data.get("minimal_logging") else 0,
            1 if data.get("delete_raw_after_extract") else 0,
            session["user_id"],
        )
    )
    get_db().commit()
    return jsonify({"ok": True})


# ── Privacy pages ─────────────────────────────────────────────────────────────

@app.route("/privacy/audit-log")
@require_login
def privacy_audit_log():
    uid = session["user_id"]
    db  = get_db()
    rows = db.execute(
        "SELECT event_type, source, domain, detail, created_at FROM privacy_audit_log "
        "WHERE user_id=? ORDER BY created_at DESC LIMIT 200",
        (uid,)
    ).fetchall()
    # Compute stats
    domain_count = db.execute(
        "SELECT COUNT(DISTINCT source) FROM account_data WHERE user_id=?", (uid,)
    ).fetchone()[0] or 0
    pages_this_month = db.execute(
        "SELECT COUNT(*) FROM privacy_audit_log WHERE user_id=? AND created_at >= datetime('now','-30 days')",
        (uid,)
    ).fetchone()[0] or 0
    total_events = db.execute(
        "SELECT COUNT(*) FROM privacy_audit_log WHERE user_id=?", (uid,)
    ).fetchone()[0] or 0

    rows_html = ""
    for r in rows:
        rows_html += (
            f'<tr><td style="padding:8px 12px;color:#6b7280;font-size:12px">{r["created_at"][:19]}</td>'
            f'<td style="padding:8px 12px;font-size:13px">{he(r["event_type"])}</td>'
            f'<td style="padding:8px 12px;font-size:13px;color:#374151">{he(r["source"] or "")}</td>'
            f'<td style="padding:8px 12px;font-size:12px;color:#9ca3af">{he(r["detail"] or "")}</td></tr>'
        )
    if not rows_html:
        rows_html = '<tr><td colspan="4" style="padding:20px;text-align:center;color:#9ca3af;font-size:13px">No events recorded yet</td></tr>'

    stats_bar = f"""
<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:20px">
  <div style="background:#fff;border-radius:8px;padding:14px 16px;box-shadow:0 1px 2px rgba(0,0,0,.06)">
    <div style="font-size:22px;font-weight:700;color:#111">{domain_count}</div>
    <div style="font-size:12px;color:#6b7280;margin-top:2px">Connected accounts</div>
  </div>
  <div style="background:#fff;border-radius:8px;padding:14px 16px;box-shadow:0 1px 2px rgba(0,0,0,.06)">
    <div style="font-size:22px;font-weight:700;color:#111">{pages_this_month}</div>
    <div style="font-size:12px;color:#6b7280;margin-top:2px">Sync events this month</div>
  </div>
  <div style="background:#fff;border-radius:8px;padding:14px 16px;box-shadow:0 1px 2px rgba(0,0,0,.06)">
    <div style="font-size:22px;font-weight:700;color:#111">{total_events}</div>
    <div style="font-size:12px;color:#6b7280;margin-top:2px">Total events logged</div>
  </div>
</div>"""

    delete_btn = """
<div style="margin-bottom:20px;display:flex;gap:10px;align-items:center">
  <button onclick="deleteAllRaw()" style="padding:8px 14px;background:#fee2e2;color:#b91c1c;border:1px solid #fca5a5;border-radius:6px;font-size:13px;cursor:pointer;font-weight:500">
    🗑 Delete all raw captures
  </button>
  <span style="font-size:12px;color:#9ca3af">Removes raw page text from all synced accounts. Extracted fields are preserved.</span>
</div>
<script>
async function deleteAllRaw() {
  if (!confirm('Delete all stored raw page text? Extracted fields will not be affected.')) return;
  const r = await fetch('/api/privacy/delete-raw-captures', {method:'POST'});
  const d = await r.json();
  alert(d.deleted + ' raw captures deleted.');
}
</script>"""

    return render_template_string("""<!DOCTYPE html><html><head><title>Audit Log — Mighty</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{font-family:-apple-system,sans-serif;margin:0;background:#f9fafb}
    .container{max-width:900px;margin:0 auto;padding:24px}</style></head>
    <body><div class="container">
    <div style="margin-bottom:20px"><a href="/settings" style="color:#6b7280;text-decoration:none;font-size:13px">← Settings</a></div>
    <h2 style="font-size:20px;font-weight:700;color:#111;margin:0 0 4px">Privacy Audit Log</h2>
    <p style="font-size:13px;color:#6b7280;margin:0 0 20px">All data capture and sync events for your account.</p>
    """ + stats_bar + delete_btn + """
    <table style="width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.08)">
    <thead><tr style="background:#f3f4f6">
    <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600">Time</th>
    <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600">Event</th>
    <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600">Account</th>
    <th style="padding:10px 12px;text-align:left;font-size:12px;color:#6b7280;font-weight:600">Detail</th>
    </tr></thead><tbody>""" + rows_html + """</tbody></table>
    </div></body></html>""")


@app.route("/api/privacy/delete-raw-captures", methods=["POST"])
@require_login
def api_delete_raw_captures():
    check_csrf()
    uid = session["user_id"]
    db = get_db()
    rows = db.execute(
        "SELECT source, data_enc FROM account_data WHERE user_id=?", (uid,)
    ).fetchall()
    deleted = 0
    for row in rows:
        try:
            d = decrypt_account_data(uid, row["data_enc"] or "")
            if d.get("raw_text"):
                d["raw_text"] = ""
                db.execute(
                    "UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
                    (encrypt_account_data(uid, d), uid, row["source"])
                )
                deleted += 1
        except Exception:
            pass
    db.commit()
    _log_privacy_event(uid, "delete_all_raw", detail=f"{deleted} accounts cleared")
    return jsonify({"ok": True, "deleted": deleted})


@app.route("/privacy/domains")
@require_login
def privacy_domains():
    uid = session["user_id"]
    rows = get_db().execute(
        "SELECT DISTINCT source, display_name FROM account_data WHERE user_id=?", (uid,)
    ).fetchall()
    approved = {r["domain"] for r in get_db().execute(
        "SELECT domain FROM approved_domains WHERE user_id=? AND approved=1", (uid,)
    ).fetchall()}

    items_html = ""
    for r in rows:
        items_html += (
            f'<div style="display:flex;align-items:center;justify-content:space-between;'
            f'padding:10px 0;border-bottom:1px solid #f3f4f6">'
            f'<div><div style="font-size:13px;font-weight:500;color:#111">{he(r["display_name"])}</div>'
            f'<div style="font-size:12px;color:#9ca3af">{he(r["source"])}</div></div>'
            f'<span style="font-size:12px;color:#22c55e;font-weight:500">✓ Approved</span></div>'
        )
    if not items_html:
        items_html = '<div style="padding:20px;text-align:center;color:#9ca3af;font-size:13px">No captured accounts yet</div>'

    return render_template_string("""<!DOCTYPE html><html><head><title>Domains — Mighty</title>
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <style>body{font-family:-apple-system,sans-serif;margin:0;background:#f9fafb}
    .container{max-width:700px;margin:0 auto;padding:24px}</style></head>
    <body><div class="container">
    <div style="margin-bottom:20px"><a href="/settings" style="color:#6b7280;text-decoration:none;font-size:13px">← Settings</a></div>
    <h2 style="font-size:20px;font-weight:700;color:#111;margin:0 0 4px">Captured Domains</h2>
    <p style="font-size:13px;color:#6b7280;margin:0 0 20px">Accounts whose data has been captured. Unknown domains are restricted to 500-character snippets only.</p>
    <div style="background:#fff;border-radius:10px;padding:0 16px;box-shadow:0 1px 3px rgba(0,0,0,.08)">""" + items_html + """</div>
    </div></body></html>""")


# ── Onboarding wizard ────────────────────────────────────────────────────────

@app.route("/onboarding")
@require_login
def onboarding():
    user = get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not user:
        session.clear()
        return redirect("/login")
    url  = base_url()
    import json as _json
    onboarding_data = _json.dumps({
        "api_key":  user["api_key"],
        "base_url": url,
    })
    return ONBOARDING_HTML.replace("MIGHTY_ONBOARDING_DATA", onboarding_data)

@app.route("/onboarding/complete", methods=["POST"])
@require_login
def onboarding_complete():
    check_csrf()
    get_db().execute("UPDATE users SET onboarded=1 WHERE id=?", (session["user_id"],))
    get_db().commit()
    return jsonify({"ok": True})

@app.route("/api/onboarding/complete", methods=["POST"])
@require_login
def api_onboarding_complete():
    check_csrf()
    get_db().execute("UPDATE users SET onboarded=1 WHERE id=?", (session["user_id"],))
    get_db().commit()
    return jsonify({"ok": True})

@app.route("/onboarding/generate-prompt", methods=["POST"])
@require_login
def onboarding_generate_prompt():
    data        = request.get_json(force=True)
    description = (data.get("description") or "").strip()
    user        = get_db().execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    url         = base_url()
    api_key     = user["api_key"]
    if not description or not ANTHROPIC_API_KEY:
        return jsonify({"prompt": build_prompt(api_key, url), "warning": None})
    try:
        result = call_claude_for_prompt(description, api_key, url)
        # Ensure both expected keys are present
        return jsonify({
            "prompt":  result.get("prompt",  build_prompt(api_key, url)),
            "warning": result.get("warning", None),
        })
    except Exception:
        return jsonify({"prompt": build_prompt(api_key, url), "warning": None})

@app.route("/onboarding/skip")
@require_login
def onboarding_skip():
    get_db().execute("UPDATE users SET onboarded=1 WHERE id=?", (session["user_id"],))
    get_db().commit()
    return redirect("/dashboard")


# ── Token-based approval page (no login required) ─────────────────────────────

@app.route("/approve/<token>", methods=["GET"])
def approve_page(token):
    expire_pending()
    db  = get_db()
    row = db.execute("SELECT * FROM actions WHERE approval_token=?", (token,)).fetchone()
    if not row:
        body = '<div class="outcome timeout" style="font-size:20px;padding:28px 20px 8px">Request not found</div><div style="text-align:center;padding:4px 20px 24px;font-size:14px;color:#6b7280">This link may have expired or already been used. You can close this tab.</div>'
        return APPROVE_HTML.replace("{body}", body)
    if row["status"] != "pending":
        labels = {"approved": "✓ Approved", "denied": "✗ Denied", "timeout": "Timed out"}
        label  = labels.get(row["status"], he(row["status"].title()))
        sublabels = {"approved": "Your agent proceeded.", "denied": "Your agent was stopped.", "timeout": "This request expired."}
        sub = sublabels.get(row["status"], "")
        body   = (f'<div class="outcome {he(row["status"])}" style="font-size:22px;padding:28px 20px 8px">{label}</div>'
                  f'<div style="text-align:center;padding:4px 20px 24px;font-size:14px;color:#6b7280">{sub} You can close this tab.</div>')
        return APPROVE_HTML.replace("{body}", body).replace(
            '<div id="agent-waiting-note"',
            '<div id="agent-waiting-note" style="display:none"'
        )
    # Build fields HTML
    fields_html = ""
    if row["fields"]:
        try:
            for k, v in json.loads(row["fields"]):
                val = v if isinstance(v, str) else json.dumps(v)
                fields_html += f'<div style="margin-bottom:12px"><div class="field-label">{he(k)}</div><div class="field-value">{he(val)}</div></div>'
        except Exception:
            pass
    expires_at_val = row["expires_at"] or ""
    body = f"""
      <div class="card-header"><div class="card-header-dot"></div><span class="card-header-text">Authorization Required</span></div>
      <div class="card-headline">{he(row["label"])}</div>
      <div class="card-type">{he(row["action_type"])}</div>
      {'<div class="card-fields">' + fields_html + '</div>' if fields_html else ''}
      <div class="card-actions">
        <button class="btn-approve" onclick="submit('approve')">Approve</button>
        <button class="btn-deny"    onclick="submit('deny')">Deny</button>
      </div>
      <div class="timeout-note">This request will time out in 5 minutes if not decided. Press <kbd style="font-family:monospace;font-size:11px;padding:1px 5px;background:#f3f4f6;border:1px solid #d1d5db;border-radius:3px">A</kbd> to approve or <kbd style="font-family:monospace;font-size:11px;padding:1px 5px;background:#f3f4f6;border:1px solid #d1d5db;border-radius:3px">D</kbd> to deny.</div>
      <div id="expiry-timer" style="text-align:center;padding:0 20px 14px;font-size:12px;color:#aaa"></div>
      <script>
      function submit(dec) {{
        document.querySelectorAll(".btn-approve, .btn-deny").forEach(function(b) {{ b.disabled = true; }});
        fetch('/approve/{token}', {{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{decision:dec}})}})
          .then(function(r) {{
            if (!r.ok) {{
              document.getElementById("agent-waiting-note").style.display = "none";
              document.querySelector(".card-actions").innerHTML = '<div style="text-align:center;padding:20px;font-size:14px;color:#9ca3af">This request has already expired or been decided.</div>';
              return;
            }}
            return r.json();
          }}).then(function(d) {{
            if (!d) return;
            var isApproved = d.status === 'approved';
            document.querySelector('.card').innerHTML =
              '<div class="outcome ' + d.status + '" style="font-size:22px;padding:28px 20px 12px">'
              + (isApproved ? '✓ Approved' : '✗ Denied') + '</div>'
              + '<div style="text-align:center;padding:4px 20px 24px;font-size:14px;color:#6b7280">'
              + (isApproved ? 'Your agent will proceed.' : 'Your agent has been stopped.')
              + ' You can close this tab.</div>';
            var note = document.getElementById('agent-waiting-note');
            if (note) note.style.display = 'none';
          }});
      }}
      (function() {{
        var expiresAt = new Date('{expires_at_val}');
        function updateTimer() {{
          var el = document.getElementById('expiry-timer');
          var now = new Date();
          var diffMs = expiresAt - now;
          if (diffMs <= 0) {{
            document.querySelector('.card').innerHTML =
              '<div class="outcome timeout" style="font-size:20px;padding:28px 20px 8px">Request timed out</div>'
              + '<div style="text-align:center;padding:4px 20px 24px;font-size:14px;color:#6b7280">This request expired. You can close this tab.</div>';
            var note = document.getElementById('agent-waiting-note');
            if (note) note.style.display = 'none';
            return;
          }}
          var mins = Math.floor(diffMs / 60000);
          var secs = Math.floor((diffMs % 60000) / 1000);
          if (el) el.textContent = 'Expires in ' + mins + 'm ' + secs + 's';
          setTimeout(updateTimer, 1000);
        }}
        updateTimer();
      }})();
      document.addEventListener('keydown', function(e) {{
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
        if (e.key === 'a' || e.key === 'A') {{
          var btn = document.querySelector('.btn-approve');
          if (btn && !btn.disabled) btn.click();
        }} else if (e.key === 'd' || e.key === 'D') {{
          var btn = document.querySelector('.btn-deny');
          if (btn && !btn.disabled) btn.click();
        }}
      }});
      </script>"""
    return APPROVE_HTML.replace("{body}", body)

@app.route("/approve/<token>", methods=["POST"])
def approve_submit(token):
    data     = request.get_json(force=True, silent=True) or {}
    decision = data.get("decision")
    if decision not in ("approve", "deny"):
        return jsonify({"error": "invalid"}), 400
    status = "approved" if decision == "approve" else "denied"
    db = get_db()
    res = db.execute(
        "UPDATE actions SET status=?, decided_at=? WHERE approval_token=? AND status='pending'",
        (status, iso(), token),
    )
    db.commit()
    if res.rowcount == 0:
        return jsonify({"error": "not found or already decided"}), 404
    return jsonify({"status": status})


# ── API ───────────────────────────────────────────────────────────────────────

@app.route("/api/record", methods=["POST"])
def api_record():
    """Log a completed action — no approval needed."""
    user, data = api_user()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401
    if not _rate_limit(user["id"], "api_record", limit=200, window=60):
        return jsonify({"error": "Rate limit exceeded"}), 429
    action_type       = data.get("action_type", "other")
    label             = data.get("label", "Action")
    fields            = data.get("fields")
    outcome           = data.get("outcome", "completed")
    consequence_level = data.get("consequence_level", "routine")
    if user["minimal_logging"]:
        label  = action_type
        fields = None
    action_id         = secrets.token_hex(16)
    get_db().execute(
        "INSERT INTO actions (id,user_id,action_type,label,fields,status,outcome,consequence_level,created_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (action_id, user["id"], action_type, label,
         json.dumps(fields) if fields else None, "logged", outcome, consequence_level, iso()),
    )
    get_db().commit()
    return jsonify({"status": "logged", "record_id": action_id})

@app.route("/api/authorize", methods=["POST"])
def api_authorize():
    """Request authorization for a consequential action. Returns pending + approval URL."""
    user, data = api_user()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401
    if not _rate_limit(user["id"], "api_authorize", limit=100, window=60):
        return jsonify({"error": "Rate limit exceeded"}), 429
    action_type       = data.get("action_type", "other")
    label             = data.get("label", "Action")
    fields            = data.get("fields")
    consequence_level = data.get("consequence_level", "routine")
    if user["minimal_logging"]:
        label  = action_type
        fields = None
    action_id         = secrets.token_hex(16)
    approval_token    = secrets.token_urlsafe(24)
    expires_at        = (utcnow() + timedelta(seconds=TIMEOUT_SEC)).isoformat()
    get_db().execute(
        "INSERT INTO actions "
        "(id,user_id,action_type,label,fields,status,approval_token,consequence_level,created_at,expires_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (action_id, user["id"], action_type, label,
         json.dumps(fields) if fields else None,
         "pending", approval_token, consequence_level, iso(), expires_at),
    )
    get_db().commit()
    url          = base_url()
    approval_url = f"{url}/approve/{approval_token}"
    # Push notification via ntfy.sh (if user has enabled it)
    if user["notify_ntfy"]:
        send_ntfy_notification(
            api_key=user["api_key"],
            label=label,
            action_type=action_type,
            approval_url=approval_url,
        )
    # Email notification via Postmark (if user has enabled it)
    if user["notify_email"]:
        send_authorization_email(
            to_email=NOTIFY_EMAIL_OVERRIDE or user["email"],
            label=label,
            action_type=action_type,
            fields=fields,
            approval_url=approval_url,
        )
    # Web Push notification
    if user["notify_push"]:
        send_web_push(
            user_id=user["id"],
            title=f"Action needed: {action_type}",
            body=label,
            url=approval_url,
            action_id=action_id,
        )
    return jsonify({
        "status":     "pending",
        "request_id": action_id,
        "poll_url":   f"{url}/api/status/{action_id}",
        "expires_in": TIMEOUT_SEC,
        "message":    "Authorization request created. Now ask the user 'Shall I proceed?' and wait for their response. Then call mighty_decide with this request_id and decision 'approved' or 'denied' based on what they say.",
    })

@app.route("/api/status/<action_id>", methods=["GET"])
def api_status(action_id):
    """Poll for the status of a pending authorization."""
    key = request.headers.get("X-Mighty-Key") or request.args.get("api_key", "")
    user = get_db().execute("SELECT * FROM users WHERE api_key=?", (key,)).fetchone()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401
    expire_pending()
    row = get_db().execute(
        "SELECT status, decided_at FROM actions WHERE id=? AND user_id=?",
        (action_id, user["id"]),
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({"status": row["status"], "decided_at": row["decided_at"]})


@app.route("/api/decide", methods=["POST"])
def api_decide():
    """Record the user's inline approval decision (approved or denied).
    Used when the user approves/denies directly in the chat rather than on the dashboard.
    """
    user, data = api_user()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401
    request_id = data.get("request_id", "")
    decision   = data.get("decision", "")
    if not request_id:
        return jsonify({"error": "request_id is required"}), 400
    if decision not in ("approved", "denied"):
        return jsonify({"error": "decision must be 'approved' or 'denied'"}), 400
    row = get_db().execute(
        "SELECT id FROM actions WHERE id=? AND user_id=?",
        (request_id, user["id"]),
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    get_db().execute(
        "UPDATE actions SET status=?, decided_at=? WHERE id=?",
        (decision, iso(), request_id),
    )
    get_db().commit()
    return jsonify({"status": decision, "request_id": request_id})


@app.route("/api/log-decision", methods=["POST"])
def api_log_decision():
    """Single-call endpoint for inline chat approval flows.
    Creates an action record and immediately sets the decision in one step.
    Avoids the two-step authorize + decide pattern.
    """
    user, data = api_user()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401
    if not _rate_limit(user["id"], "api_log_decision", limit=100, window=60):
        return jsonify({"error": "Rate limit exceeded"}), 429
    action_type       = data.get("action_type", "other")
    label             = data.get("label", "Action")
    fields            = data.get("fields")
    decision          = data.get("decision", "")
    consequence_level = data.get("consequence_level", "routine")
    if decision not in ("approved", "denied"):
        return jsonify({"error": "decision must be 'approved' or 'denied'"}), 400
    action_id = secrets.token_hex(16)
    now       = iso()
    if user["minimal_logging"]:
        label  = action_type  # store only the category, not the full description
        fields = None
    get_db().execute(
        "INSERT INTO actions "
        "(id,user_id,action_type,label,fields,status,consequence_level,created_at,decided_at) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (action_id, user["id"], action_type, label,
         json.dumps(fields) if fields else None,
         decision, consequence_level, now, now),
    )
    get_db().commit()
    return jsonify({"status": decision, "record_id": action_id})


# ── Service worker ────────────────────────────────────────────────────────────

SW_JS = r"""
self.addEventListener('push', function(e) {
  var data = e.data ? e.data.json() : {};
  var title   = data.title   || 'Action needed';
  var body    = data.body    || 'Your agent needs permission.';
  var url     = data.url     || '/dashboard';
  var actions = data.actions || [];
  e.waitUntil(
    self.registration.showNotification(title, {
      body:    body,
      icon:    '/logo-icon.png',
      badge:   '/logo-icon.png',
      tag:     data.tag || 'mighty-auth',
      renotify: true,
      requireInteraction: true,
      actions: actions,
      data:    { url: url }
    })
  );
});

self.addEventListener('notificationclick', function(e) {
  e.notification.close();
  var url = e.notification.data.url;
  if (e.action === 'deny') {
    fetch(url, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({decision:'deny'})}).catch(function(){});
    return;
  }
  e.waitUntil(clients.openWindow(e.notification.data.url));
});
"""

@app.route("/sw.js")
def service_worker():
    resp = make_response(SW_JS)
    resp.headers["Content-Type"] = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    return resp


# ── Push subscription endpoints ───────────────────────────────────────────────

@app.route("/api/push/subscribe", methods=["POST"])
@require_login
def push_subscribe():
    check_csrf()
    data = request.get_json(force=True)
    sub  = data.get("subscription")
    if not sub:
        return jsonify({"error": "missing subscription"}), 400
    sub_str  = json.dumps(sub)
    endpoint = sub.get("endpoint", "")
    db = get_db()
    # Check if subscription with this endpoint already exists for the user
    existing = None
    if endpoint:
        rows = db.execute(
            "SELECT id, subscription FROM push_subscriptions WHERE user_id=?",
            (session["user_id"],)
        ).fetchall()
        for r in rows:
            try:
                if json.loads(r["subscription"]).get("endpoint") == endpoint:
                    existing = r
                    break
            except Exception:
                pass
    if existing:
        # Update the existing record (keys may have rotated)
        db.execute(
            "UPDATE push_subscriptions SET subscription=?, created_at=? WHERE id=?",
            (sub_str, iso(), existing["id"])
        )
    else:
        sub_id = secrets.token_hex(8)
        db.execute(
            "INSERT INTO push_subscriptions (id,user_id,subscription,created_at) VALUES (?,?,?,?)",
            (sub_id, session["user_id"], sub_str, iso())
        )
    db.commit()
    return jsonify({"ok": True})

@app.route("/api/push/unsubscribe", methods=["POST"])
@require_login
def push_unsubscribe():
    check_csrf()
    get_db().execute("DELETE FROM push_subscriptions WHERE user_id=?", (session["user_id"],))
    get_db().commit()
    return jsonify({"ok": True})

@app.route("/api/push/vapid-public-key")
def vapid_public_key():
    if not VAPID_PUBLIC:
        return jsonify({"key": None, "disabled": True}), 503
    return jsonify({"key": VAPID_PUBLIC})


# ── Health check ──────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    try:
        user_count = get_db().execute("SELECT COUNT(*) FROM users").fetchone()[0]
        db_ok = True
    except Exception as e:
        user_count = None
        db_ok = False
    import subprocess, os
    try:
        git_sha = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                          cwd=os.path.dirname(__file__) or ".",
                                          stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        git_sha = "unknown"
    return jsonify({
        "ok":         True,
        "db_ok":      db_ok,
        "user_count": user_count,
        "git_sha":    git_sha,
    })


@app.route("/api/ping", methods=["POST"])
def api_ping():
    """Verify an API key and return the associated account email (masked).
    Useful for debugging key/account mismatches.
    """
    user, _ = api_user()
    if not user:
        return jsonify({"ok": False, "error": "Invalid or missing api_key"}), 401
    email = user["email"]
    # Mask email: j****@example.com
    local, _, domain = email.partition("@")
    masked = local[0] + ("*" * max(1, len(local) - 1)) + "@" + domain
    return jsonify({"ok": True, "account": masked})


# ── Credential management ─────────────────────────────────────────────────────

# Available data fields per account — shown as checkboxes in /credentials
ACCOUNT_FIELDS = {
    "pa_utilities": [
        {"key": "balance",  "label": "Current Balance",         "default": True},
        {"key": "due_date", "label": "Due Date",                "default": True},
        {"key": "auto_pay", "label": "Auto-pay Status",         "default": True},
        {"key": "usage",    "label": "Monthly kWh Usage",       "default": True},
        {"key": "alerts",   "label": "Service Alerts",          "default": True},
        {"key": "offers",   "label": "Ways to Save Offers",     "default": False},
    ],
    "delta": [
        {"key": "miles",       "label": "SkyMiles Balance",     "default": True},
        {"key": "status",      "label": "Medallion Status",     "default": True},
        {"key": "next_flight", "label": "Next Flight",          "default": True},
        {"key": "offers",      "label": "Active Promotions",    "default": False},
    ],
    "marriott": [
        {"key": "points",    "label": "Bonvoy Points",          "default": True},
        {"key": "tier",      "label": "Member Tier",            "default": True},
        {"key": "awards",    "label": "Free Night Awards",      "default": True},
        {"key": "expiry",    "label": "Points Expiry",          "default": True},
    ],
    "hilton": [
        {"key": "points",    "label": "Honors Points",          "default": True},
        {"key": "tier",      "label": "Member Tier",            "default": True},
        {"key": "awards",    "label": "Free Night Rewards",     "default": True},
    ],
    "amex": [
        {"key": "balance",   "label": "Current Balance",        "default": True},
        {"key": "due_date",  "label": "Payment Due Date",       "default": True},
        {"key": "rewards",   "label": "Membership Rewards",     "default": True},
        {"key": "offers",    "label": "Amex Offers",            "default": False},
    ],
    "chase": [
        {"key": "balance",   "label": "Balance",                "default": True},
        {"key": "due_date",  "label": "Due Date",               "default": True},
        {"key": "rewards",   "label": "Ultimate Rewards",       "default": True},
    ],
    "xfinity": [
        {"key": "balance",   "label": "Balance",                "default": True},
        {"key": "due_date",  "label": "Due Date",               "default": True},
        {"key": "data",      "label": "Data Usage",             "default": True},
    ],
    "amazon": [
        {"key": "orders",    "label": "Recent Orders",          "default": True},
        {"key": "tracking",  "label": "Active Tracking",        "default": True},
    ],
    "pamf": [
        {"key": "messages",  "label": "New Messages",           "default": True},
        {"key": "appt",      "label": "Next Appointment",       "default": True},
        {"key": "results",   "label": "Pending Results",        "default": False},
        {"key": "refills",   "label": "Prescription Refills",   "default": False},
    ],
}

LOGIN_HINTS = {
    # Airlines
    "delta": {"u": "SkyMiles number or username", "p": "Password", "u_type": "text", "url": "https://www.delta.com/us/en/login"},
    "united": {"u": "MileagePlus number or email", "p": "Password", "u_type": "text", "url": "https://www.united.com/en/us/account/login"},
    "american": {"u": "AAdvantage number or email", "p": "Password", "u_type": "text", "url": "https://www.aa.com/homePage.do"},
    "southwest": {"u": "Rapid Rewards number or email", "p": "Password", "u_type": "text", "url": "https://www.southwest.com/air/login.html"},
    "alaska": {"u": "Mileage Plan number or email", "p": "Password", "u_type": "text", "url": "https://www.alaskaair.com/account/login"},
    "jetblue": {"u": "TrueBlue number or email", "p": "Password", "u_type": "text", "url": "https://www.jetblue.com/trueblue/sign-in"},
    "spirit": {"u": "Free Spirit number or email", "p": "Password", "u_type": "text", "url": "https://www.spirit.com/account/sign-in"},
    "frontier": {"u": "FRONTIER Miles number or email", "p": "Password", "u_type": "text", "url": "https://www.flyfrontier.com/account/login"},
    "hawaiian": {"u": "HawaiianMiles number or email", "p": "Password", "u_type": "text", "url": "https://www.hawaiianairlines.com/my-account/login"},
    "sun_country": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.suncountry.com/account/login"},
    "breeze": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.flybreeze.com/account/login"},
    "avelo": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.aveloair.com/sign-in"},
    "allegiant": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.allegiantair.com/account/login"},
    "cape_air": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.capeair.com/myaccount"},
    "silver_airways": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.silverairways.com/account/login"},
    # Hotels & Hospitality
    "marriott": {"u": "Marriott Bonvoy number or email", "p": "Password", "u_type": "text", "url": "https://www.marriott.com/loyalty/loginPage.mi"},
    "hilton": {"u": "Email address or Hilton Honors number", "p": "Password", "u_type": "text", "url": "https://www.hilton.com/en/hilton-honors/login/"},
    "hyatt": {"u": "World of Hyatt number or email", "p": "Password", "u_type": "text", "url": "https://www.hyatt.com/en-US/member/login"},
    "ihg": {"u": "IHG One Rewards number or email", "p": "Password", "u_type": "text", "url": "https://www.ihg.com/rewardsclub/content/us/en/login"},
    "wyndham": {"u": "Wyndham Rewards member number or email", "p": "Password", "u_type": "text", "url": "https://www.wyndhamhotels.com/wyndham-rewards/login"},
    "choice_hotels": {"u": "Choice Privileges number or email", "p": "Password", "u_type": "text", "url": "https://www.choicehotels.com/sign-in"},
    "best_western": {"u": "Best Western Rewards number or email", "p": "Password", "u_type": "text", "url": "https://www.bestwestern.com/en_US/best-western-rewards/sign-in.html"},
    "radisson": {"u": "Radisson Rewards number or email", "p": "Password", "u_type": "text", "url": "https://www.radissonhotels.com/en-us/login"},
    "omni": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.omnihotels.com/login"},
    "loews": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.loewshotels.com/sign-in"},
    "kimpton": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.kimptonhotels.com/login"},
    "four_seasons": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.fourseasons.com/account/login/"},
    "ritz_carlton": {"u": "Marriott Bonvoy number or email", "p": "Password", "u_type": "text", "url": "https://www.ritzcarlton.com/loyalty/loginPage.mi"},
    "airbnb": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.airbnb.com/login"},
    "vrbo": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.vrbo.com/account/login"},
    "hotels_com": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.hotels.com/account/login/"},
    "expedia": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.expedia.com/login"},
    "booking_com": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://account.booking.com/sign-in"},
    "priceline": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.priceline.com/user/login"},
    "kayak": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.kayak.com/user/login"},
    "trivago": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.trivago.com/account/login"},
    "travelocity": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.travelocity.com/login"},
    # Rental Cars
    "enterprise": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.enterprise.com/en/car-rental/profile/login.html"},
    "hertz": {"u": "Email address or Gold Plus Rewards number", "p": "Password", "u_type": "text", "url": "https://www.hertz.com/rentacar/member/login.jsp"},
    "avis": {"u": "Email address or Avis Preferred number", "p": "Password", "u_type": "text", "url": "https://www.avis.com/en/profile/login"},
    "budget": {"u": "Email address or Budget Fastbreak number", "p": "Password", "u_type": "text", "url": "https://www.budget.com/en/profile/login"},
    "national": {"u": "Email address or Emerald Club number", "p": "Password", "u_type": "text", "url": "https://www.nationalcar.com/en/car-rental/profile/login.html"},
    "alamo": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.alamo.com/en_US/car-rental/profile/login.html"},
    "dollar": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.dollar.com/en/profile/login"},
    "thrifty": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.thrifty.com/en/profile/login"},
    "sixt": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.sixt.com/account/login/"},
    "turo": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://turo.com/login"},
    "zipcar": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.zipcar.com/en-us/login"},
    # Banking
    "chase": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://secure.chase.com/"},
    "bofa": {"u": "Online ID", "p": "Passcode", "u_type": "text", "url": "https://www.bankofamerica.com/online-banking/sign-in.go"},
    "wells_fargo": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://connect.secure.wellsfargo.com/auth/login/present"},
    "citi": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://online.citi.com/US/login.do"},
    "usbank": {"u": "Online ID", "p": "Password", "u_type": "text", "url": "https://onlinebanking.usbank.com/auth/login"},
    "truist": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.truist.com/online-banking"},
    "pnc": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.pnc.com/en/personal-banking/bank/online-banking.html"},
    "td_bank": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.td.com/us/en/personal-banking/online-banking/"},
    "capital_one_bank": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://verified.capitalone.com/auth/signin"},
    "regions": {"u": "Online ID", "p": "Password", "u_type": "text", "url": "https://www.regions.com/online-banking"},
    "fifth_third": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.53.com/content/fifth-third/en/personal-banking/bank.html"},
    "keybank": {"u": "Key ID", "p": "Password", "u_type": "text", "url": "https://www.key.com/personal/banking/online-banking.jsp"},
    "huntington": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.huntington.com/personal/banking/online-banking"},
    "citizens_bank": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.citizensbank.com/account/login.aspx"},
    "m_and_t": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.mtb.com/personal/online-and-mobile-banking"},
    "ally": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://secure.ally.com/"},
    "discover_bank": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://portal.discover.com/"},
    "synchrony": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.synchronybank.com/banking/sign-in.html"},
    "navy_federal": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.navyfederal.org/"},
    "usaa": {"u": "Online ID", "p": "Password", "u_type": "text", "url": "https://www.usaa.com/inet/ent_logon/Logon"},
    "chime": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.chime.com/login"},
    "sofi": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.sofi.com/login/"},
    "marcus": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.marcus.com/us/en/login"},
    "bmo_harris": {"u": "Online ID", "p": "Password", "u_type": "text", "url": "https://www.bmoharris.com/main/personal/"},
    "comerica": {"u": "Access ID", "p": "Password", "u_type": "text", "url": "https://www.comerica.com/"},
    "zions": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.zionsbank.com/"},
    "glacier_bank": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.glacierbank.com/"},
    "bank_of_the_west": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.bankofthewest.com/"},
    "new_york_community_bank": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.mynycb.com/"},
    "nbkc": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.nbkc.com/"},
    "umpqua": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.umpquabank.com/"},
    "first_republic": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.firstrepublic.com/"},
    # Credit Cards
    "amex": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.americanexpress.com/en-us/account/login"},
    "capital_one": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://verified.capitalone.com/auth/signin"},
    "discover": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://portal.discover.com/"},
    "barclays": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.barclaycardus.com/banking/login.action"},
    "synchrony_credit": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://mysynchrony.com/"},
    "apple_card": {"u": "Apple ID", "p": "Password", "u_type": "email", "url": "https://appleid.apple.com/"},
    "paypal_credit": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.paypal.com/signin"},
    "bread_financial": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://d.comenity.net/"},
    "comenity": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://d.comenity.net/"},
    # Investment / Brokerage
    "fidelity": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.fidelity.com/"},
    "vanguard": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://investor.vanguard.com/home/"},
    "schwab": {"u": "Login ID", "p": "Password", "u_type": "text", "url": "https://www.schwab.com/"},
    "etrade": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://us.etrade.com/e/t/user/login"},
    "td_ameritrade": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.tdameritrade.com/account-login.html"},
    "robinhood": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://robinhood.com/login"},
    "merrill": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.merrilledge.com/login"},
    "edward_jones": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.edwardjones.com/us-en/login"},
    "raymond_james": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.raymondjames.com/"},
    "wells_fargo_invest": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.wellsfargoadvisors.com/"},
    "morgan_stanley": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://login.morganstanley.com/"},
    "interactive_brokers": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.interactivebrokers.com/en/trading/login.php"},
    "tastytrade": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://my.tastytrade.com/"},
    "webull": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.webull.com/"},
    "sofi_invest": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.sofi.com/login/"},
    "acorns": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.acorns.com/"},
    "betterment": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://wwws.betterment.com/login"},
    "wealthfront": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.wealthfront.com/"},
    "stash": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.stash.com/"},
    "public": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://public.com/sign-in"},
    "m1_finance": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.m1.com/"},
    "coinbase": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.coinbase.com/signin"},
    "kraken": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://www.kraken.com/sign-in"},
    "gemini": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://exchange.gemini.com/signin"},
    "ubs": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://onlineservices.ubs.com/"},
    # Insurance - Auto
    "state_farm": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.statefarm.com/"},
    "geico": {"u": "User ID or email", "p": "Password", "u_type": "text", "url": "https://service.geico.com/"},
    "progressive": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.progressive.com/logon/"},
    "allstate": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://myaccount.allstate.com/"},
    "farmers": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.farmers.com/"},
    "liberty_mutual": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.libertymutual.com/account-login"},
    "nationwide": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.nationwide.com/login/"},
    "travelers": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.travelers.com/log-in"},
    "aaa": {"u": "Email address or membership number", "p": "Password", "u_type": "text", "url": "https://www.aaa.com/signin"},
    "usaa_auto": {"u": "Online ID", "p": "Password", "u_type": "text", "url": "https://www.usaa.com/"},
    "american_family": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://myaccount.amfam.com/"},
    "erie_insurance": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.erieinsurance.com/"},
    "auto_owners": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.auto-owners.com/"},
    "the_hartford": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.thehartford.com/"},
    "mercury_insurance": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.mercuryinsurance.com/"},
    "lemonade": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.lemonade.com/sign-in"},
    "root": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.joinroot.com/"},
    "hippo": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://myhippo.com/account/login"},
    # Insurance - Health
    "cigna": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://my.cigna.com/"},
    "aetna": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.aetna.com/members-medicare/log-in.html"},
    "humana": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.humana.com/member/login"},
    "united_health": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://member.uhc.com/"},
    "anthem": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.anthem.com/member-login/"},
    "bcbs": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.bcbs.com/"},
    "kaiser": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://healthy.kaiserpermanente.org/"},
    "molina": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.molinahealthcare.com/"},
    "oscar_health": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.hioscar.com/"},
    # Insurance - Life
    "metlife": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://mybenefits.metlife.com/"},
    "prudential": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.prudential.com/"},
    "new_york_life": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.newyorklife.com/"},
    "principal": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.principal.com/"},
    "john_hancock": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.johnhancock.com/"},
    "lincoln_financial": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.lfg.com/"},
    "northwestern_mutual": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://client.northwesternmutual.com/"},
    "transamerica": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.transamerica.com/"},
    "mass_mutual": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.massmutual.com/"},
    "guardian": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.guardianlife.com/"},
    "aflac": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.aflac.com/"},
    "nationwide_life": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.nationwide.com/"},
    "securian": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.securian.com/"},
    "banner_life": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.bannerlife.com/"},
    "pacific_life": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.pacificlife.com/"},
    # Telecom - Mobile
    "att": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.att.com/acctmgmt/login"},
    "verizon": {"u": "User ID or mobile number", "p": "Password", "u_type": "text", "url": "https://www.verizon.com/home/myverizon/"},
    "tmobile": {"u": "Email address or phone number", "p": "Password", "u_type": "text", "url": "https://account.t-mobile.com/"},
    "sprint": {"u": "Username or phone number", "p": "Password", "u_type": "text", "url": "https://www.sprint.com/en/shop/account/sign-in.html"},
    "us_cellular": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.uscellular.com/my-account"},
    "cricket": {"u": "Email address or phone number", "p": "Password", "u_type": "text", "url": "https://www.cricketwireless.com/sign-in"},
    "metro": {"u": "Phone number or email", "p": "Password", "u_type": "tel", "url": "https://www.metrobyt-mobile.com/account/login"},
    "boost": {"u": "Email address or phone number", "p": "Password", "u_type": "text", "url": "https://www.boostmobile.com/account/signin/"},
    "straight_talk": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.straighttalk.com/wps/portal/home/account"},
    "visible": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.visible.com/login"},
    "mint_mobile": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://my.mintmobile.com/login"},
    "google_fi": {"u": "Google account email", "p": "Password", "u_type": "email", "url": "https://fi.google.com/about/"},
    "consumer_cellular": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.consumercellular.com/myaccount"},
    "ting": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://ting.com/login"},
    "republic_wireless": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://republicwireless.com/login"},
    # Internet & Cable
    "comcast_xfinity": {"u": "Xfinity ID or email", "p": "Password", "u_type": "text", "url": "https://login.xfinity.com/"},
    "spectrum": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.spectrum.net/login"},
    "cox": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://myaccount.cox.com/"},
    "dish": {"u": "Username or account number", "p": "Password", "u_type": "text", "url": "https://www.dish.com/account/login/"},
    "directv": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.directv.com/account/login"},
    "century_link": {"u": "User ID or email", "p": "Password", "u_type": "text", "url": "https://www.centurylink.com/local/login.html"},
    "frontier_comm": {"u": "User ID or email", "p": "Password", "u_type": "text", "url": "https://login.frontier.com/"},
    "windstream": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.windstream.com/"},
    "mediacom": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://mediacomcable.com/"},
    "optimum": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://login.optimum.net/"},
    "altice": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.alticeusa.com/"},
    "earthlink": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.earthlink.net/account/"},
    "rcn": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.rcn.com/"},
    "google_fiber": {"u": "Google account email", "p": "Password", "u_type": "email", "url": "https://fiber.google.com/"},
    "sparklight": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://sparklight.com/"},
    "breezeline": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.breezeline.com/"},
    "ziply": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://ziplyfiber.com/myaccount"},
    "wow": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.wowway.com/"},
    "lumen": {"u": "User ID or email", "p": "Password", "u_type": "text", "url": "https://www.lumen.com/"},
    # Streaming & Entertainment
    "netflix": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.netflix.com/login"},
    "hulu": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://auth.hulu.com/web/login"},
    "disney_plus": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.disneyplus.com/login"},
    "hbo_max": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.max.com/sign-in"},
    "amazon_prime": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.amazon.com/ap/signin"},
    "apple_tv": {"u": "Apple ID", "p": "Password", "u_type": "email", "url": "https://appleid.apple.com/"},
    "peacock": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.peacocktv.com/signin"},
    "paramount_plus": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.paramountplus.com/account/signin/"},
    "espn_plus": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.espnplus.com/"},
    "fubo": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.fubo.tv/sign-in"},
    "sling": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://watch.sling.com/"},
    "youtube_tv": {"u": "Google account email", "p": "Password", "u_type": "email", "url": "https://tv.youtube.com/"},
    "philo": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.philo.com/"},
    "crunchyroll": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.crunchyroll.com/login"},
    "spotify": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://accounts.spotify.com/en/login"},
    "apple_music": {"u": "Apple ID", "p": "Password", "u_type": "email", "url": "https://appleid.apple.com/"},
    "tidal": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://listen.tidal.com/login"},
    "pandora": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.pandora.com/account/sign-in"},
    "amazon_music": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://music.amazon.com/"},
    "deezer": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.deezer.com/us/login"},
    "youtube": {"u": "Google account email", "p": "Password", "u_type": "email", "url": "https://www.youtube.com/"},
    "twitch": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://www.twitch.tv/login"},
    "plex": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://app.plex.tv/auth/"},
    "vudu": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.vudu.com/content/movies/uviLogin"},
    "movies_anywhere": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://moviesanywhere.com/signin"},
    "showtime": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.showtime.com/"},
    "starz": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.starz.com/"},
    "discovery_plus": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.discoveryplus.com/"},
    "criterion": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.criterionchannel.com/sign-in"},
    "mubi": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://mubi.com/"},
    "shudder": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.shudder.com/"},
    "amc_plus": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.amcplus.com/"},
    "bet_plus": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.betplus.com/"},
    "funimation": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.funimation.com/"},
    "siriusxm": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://www.siriusxm.com/sign-in"},
    "lifetime_movie_club": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.mylifetime.com/"},
    # Retail - Department/Big Box
    "target": {"u": "Email address or username", "p": "Password", "u_type": "email", "url": "https://www.target.com/login"},
    "walmart": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.walmart.com/account/login"},
    "costco": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.costco.com/LogonForm"},
    "sams_club": {"u": "Email address or username", "p": "Password", "u_type": "email", "url": "https://www.samsclub.com/auth/login"},
    "amazon": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.amazon.com/ap/signin"},
    "macys": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.macys.com/account/login"},
    "nordstrom": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.nordstrom.com/signin"},
    "nordstrom_rack": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.nordstromrack.com/login"},
    "kohls": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.kohls.com/login.jsp"},
    "jcpenney": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.jcpenney.com/signin"},
    "sears": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.sears.com/account/signin"},
    "tj_maxx": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.tjmaxx.tjx.com/store/index.jsp"},
    "marshalls": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.marshalls.com/"},
    "homegoods": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.homegoods.com/"},
    "ross": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.rossstores.com/"},
    "burlington": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.burlington.com/"},
    "belk": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.belk.com/signin/"},
    "dillards": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.dillards.com/login"},
    "bloomingdales": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.bloomingdales.com/account/sign-in"},
    "neiman_marcus": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.neimanmarcus.com/login"},
    "saks": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.saksfifthavenue.com/login"},
    "williams_sonoma": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.williams-sonoma.com/customer/account/login/"},
    "pottery_barn": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.potterybarn.com/customer/account/login/"},
    "west_elm": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.westelm.com/customer/account/login/"},
    "crate_and_barrel": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.crateandbarrel.com/customer-account/login"},
    "ikea": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.ikea.com/us/en/profile/login/"},
    "wayfair": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.wayfair.com/identity/signin"},
    "overstock": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.overstock.com/account/login"},
    "home_depot": {"u": "Email address or username", "p": "Password", "u_type": "email", "url": "https://www.homedepot.com/auth/view/signin"},
    "lowes": {"u": "Email address or username", "p": "Password", "u_type": "email", "url": "https://www.lowes.com/login"},
    "menards": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.menards.com/main/login.html"},
    "ace_hardware": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.acehardware.com/sign-in"},
    "bed_bath": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.bedbathandbeyond.com/store/account/signin"},
    "harbor_freight": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.harborfreight.com/customer/account/login/"},
    "tractor_supply": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.tractorsupply.com/tsc/catalog/my-account/sign-in"},
    # Retail - Sporting/Outdoor
    "bass_pro": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.basspro.com/shop/UserLoginView"},
    "cabelas": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.cabelas.com/shop/UserLoginView"},
    "rei": {"u": "Email address or member number", "p": "Password", "u_type": "text", "url": "https://www.rei.com/account/sign-in"},
    "dick_sporting": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.dickssportinggoods.com/signin"},
    "academy": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.academy.com/c/signin"},
    "big_5": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.big5sportinggoods.com/store/signin/"},
    "gamestop": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.gamestop.com/signin"},
    # Retail - Apparel
    "gap": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.gap.com/account/signin.do"},
    "old_navy": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://oldnavy.gap.com/account/signin.do"},
    "banana_republic": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://bananarepublic.gap.com/account/signin.do"},
    "anthropologie": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.anthropologie.com/account/login"},
    "urban_outfitters": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.urbanoutfitters.com/account/login"},
    "free_people": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.freepeople.com/account/login"},
    "h_and_m": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www2.hm.com/en_us/member/login.html"},
    "zara": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.zara.com/us/en/logon"},
    "uniqlo": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.uniqlo.com/us/en/login"},
    "express": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.express.com/signin"},
    "torrid": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.torrid.com/login"},
    "lane_bryant": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.lanebryant.com/account/sign-in"},
    "victorias_secret": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.victoriassecret.com/us/vs/sign-in"},
    "bath_body_works": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.bathandbodyworks.com/signin.html"},
    "sephora": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.sephora.com/profile/login"},
    "ulta": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.ulta.com/ulta/signin.jsp"},
    "foot_locker": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.footlocker.com/account/login"},
    "finish_line": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.finishline.com/store/auth/login"},
    "zappos": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.zappos.com/login"},
    "dsw": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.dsw.com/account/sign-in"},
    # Retail - Electronics/Online
    "best_buy": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.bestbuy.com/identity/global/signin"},
    "apple_store": {"u": "Apple ID", "p": "Password", "u_type": "email", "url": "https://appleid.apple.com/"},
    "microsoft_store": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://account.microsoft.com/"},
    "staples": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.staples.com/gsa/logon"},
    "office_depot": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.officedepot.com/login.do"},
    "newegg": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://secure.newegg.com/identity/signin"},
    "bhphotovideo": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://www.bhphotovideo.com/find/profile.jsp"},
    "adorama": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.adorama.com/Als.aspx"},
    "chewy": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.chewy.com/app/secure/login"},
    "etsy": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.etsy.com/signin"},
    "ebay": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://signin.ebay.com/"},
    # Retail - Auto Parts
    "auto_zone": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.autozone.com/signin"},
    "oreilly_auto": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.oreillyauto.com/"},
    "advance_auto": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://shop.advanceautoparts.com/web/SSOController"},
    "napa": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.napaonline.com/en/account"},
    # Retail - Pet
    "petco": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.petco.com/shop/en/petcostore/signin"},
    "petsmart": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.petsmart.com/account/sign-in"},
    # Grocery
    "kroger": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.kroger.com/signin"},
    "safeway": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.safeway.com/account/sign-in.html"},
    "albertsons": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.albertsons.com/account/sign-in.html"},
    "publix": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.publix.com/pub/login.do"},
    "whole_foods": {"u": "Amazon email or phone", "p": "Password", "u_type": "email", "url": "https://www.amazon.com/alm/storefront"},
    "h_e_b": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.heb.com/signin"},
    "meijer": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.meijer.com/signin"},
    "giant_food": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://giantfood.com/my-account/sign-in"},
    "stop_and_shop": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://stopandshop.com/my-account/sign-in"},
    "hannaford": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.hannaford.com/account/signin.jsp"},
    "weis": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.weismarkets.com/sign-in"},
    "food_lion": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.foodlion.com/my-account/sign-in"},
    "wegmans": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.wegmans.com/signin.html"},
    "harris_teeter": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.harristeeter.com/account/sign-in.html"},
    "giant_eagle": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.gianteagle.com/sign-in"},
    "sprouts": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.sprouts.com/"},
    "market_basket": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.marketbasket.com/"},
    "stater_bros": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.staterbros.com/"},
    "aldi": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://new.aldi.us/"},
    "trader_joes": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.traderjoes.com/"},
    # Drug Stores
    "cvs": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.cvs.com/account/login/"},
    "walgreens": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.walgreens.com/login"},
    "rite_aid": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.riteaid.com/account/sign-in"},
    "dollar_general": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.dollargeneral.com/sign-in"},
    "dollar_tree": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.dollartree.com/"},
    "family_dollar": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.familydollar.com/"},
    # Restaurants & Food Delivery
    "mcdonalds": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.mcdonalds.com/us/en-us/my-mcdonalds.html"},
    "starbucks": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.starbucks.com/account/signin"},
    "subway": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.subway.com/en-US/MyAccount/Login"},
    "chick_fil_a": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.chick-fil-a.com/one"},
    "taco_bell": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.tacobell.com/login"},
    "burger_king": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.bk.com/login"},
    "wendys": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.wendys.com/"},
    "dunkin": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.dunkindonuts.com/en/sign-in"},
    "dominos": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.dominos.com/en/"},
    "pizza_hut": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.pizzahut.com/account/signin"},
    "papa_johns": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.papajohns.com/account/login"},
    "panera": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.panerabread.com/en-us/mypanera/sign-in.html"},
    "chipotle": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.chipotle.com/login"},
    "sonic": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.sonicdrivein.com/account/signin"},
    "popeyes": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.popeyes.com/"},
    "ihop": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.ihop.com/en/account/login"},
    "olive_garden": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.olivegarden.com/"},
    "red_lobster": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.redlobster.com/"},
    "applebees": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.applebees.com/"},
    "denny_s": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.dennys.com/"},
    "outback": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.outback.com/"},
    "cracker_barrel": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.crackerbarrel.com/"},
    "texas_roadhouse": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.texasroadhouse.com/"},
    "five_guys": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.fiveguys.com/"},
    "shake_shack": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.shakeshack.com/"},
    "sweetgreen": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://order.sweetgreen.com/"},
    "panda_express": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.pandaexpress.com/account/login"},
    "jimmy_johns": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.jimmyjohns.com/"},
    "jersey_mikes": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.jerseymikes.com/"},
    "firehouse_subs": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://firehousesubs.com/"},
    "wingstop": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.wingstop.com/"},
    "buffalo_wild_wings": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.buffalowildwings.com/"},
    "noodles": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.noodles.com/"},
    "moes": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.moes.com/"},
    "doordash": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.doordash.com/login/"},
    "uber_eats": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://auth.uber.com/"},
    "grubhub": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.grubhub.com/login"},
    "instacart": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.instacart.com/login"},
    "postmates": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://postmates.com/"},
    "gopuff": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.gopuff.com/go/account"},
    "seamless": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.seamless.com/login"},
    "caviar": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.trycaviar.com/"},
    # Gas & Fuel Rewards
    "shell": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.shell.us/"},
    "exxon": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.exxon.com/en/account"},
    "mobil": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.mobil.com/en/account"},
    "bp": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.bpme.com/"},
    "chevron": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.chevronwithtechron.com/"},
    "texaco": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.texaco.com/"},
    "speedway": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.speedway.com/account"},
    "circle_k": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.circlek.com/inner-circle"},
    "wawa": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.wawa.com/"},
    "sheetz": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.sheetz.com/mySheetz/my-account/sign-in"},
    "kwik_trip": {"u": "Phone number or email", "p": "Password", "u_type": "text", "url": "https://www.kwiktrip.com/"},
    "maverik": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.maverik.com/"},
    "pilot_flying_j": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.pilotflyingj.com/"},
    "casey_general": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.caseys.com/account/sign-in"},
    "sunoco": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.sunoco.com/"},
    "murphy_usa": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.murphyusa.com/"},
    "racetrac": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.racetrac.com/"},
    "76_gas": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.76.com/"},
    # Healthcare - Hospital Systems
    "mychart": {"u": "MyChart username", "p": "Password", "u_type": "text", "url": "https://mychart.com/"},
    "kaiser_mychart": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://healthy.kaiserpermanente.org/"},
    "cleveland_clinic": {"u": "MyChart username", "p": "Password", "u_type": "text", "url": "https://mychart.clevelandclinic.org/"},
    "mayo_clinic": {"u": "Mayo Clinic Patient Online Services ID", "p": "Password", "u_type": "text", "url": "https://onlineservices.mayoclinic.org/"},
    "johns_hopkins": {"u": "MyChart username", "p": "Password", "u_type": "text", "url": "https://mychart.hopkinsmedicine.org/"},
    "intermountain": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://intermountainhealthcare.org/"},
    "providence": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://www.providence.org/"},
    "commonspirit": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.commonspirit.org/"},
    "hca": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://hcahealthcare.com/"},
    "ascension": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://healthcare.ascension.org/"},
    "banner_health": {"u": "MyChart username", "p": "Password", "u_type": "text", "url": "https://www.bannerhealth.com/"},
    "advocate_health": {"u": "MyChart username", "p": "Password", "u_type": "text", "url": "https://www.advocateaurorahealth.org/"},
    "northwell": {"u": "MyChart username", "p": "Password", "u_type": "text", "url": "https://northwell.edu/"},
    "prisma_health": {"u": "MyChart username", "p": "Password", "u_type": "text", "url": "https://www.prismahealth.org/"},
    "tenet_health": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.tenethealth.com/"},
    # Healthcare - Pharmacies
    "cvs_pharmacy": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.cvs.com/account/login/"},
    "walgreens_rx": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.walgreens.com/login"},
    "express_scripts": {"u": "Member ID or username", "p": "Password", "u_type": "text", "url": "https://www.express-scripts.com/pharmacy/sign-in/"},
    "caremark": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.caremark.com/"},
    "optum_rx": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.optumrx.com/"},
    "goodrx": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.goodrx.com/account/sign-in"},
    "capsule": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.capsule.com/"},
    "amazon_pharmacy": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://pharmacy.amazon.com/"},
    # Healthcare - Telehealth
    "teladoc": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.teladoc.com/members/sign-in"},
    "mdlive": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://mdlive.com/patient-login"},
    "one_medical": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.onemedical.com/"},
    "ro": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://ro.co/"},
    "hims": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.forhims.com/"},
    "nurx": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.nurx.com/"},
    "zocdoc": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.zocdoc.com/login"},
    # Utilities - Electric
    "duke_energy": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.duke-energy.com/sign-in"},
    "dominion_energy": {"u": "Username or account number", "p": "Password", "u_type": "text", "url": "https://www.dominionenergy.com/"},
    "georgia_power": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.georgiapower.com/"},
    "alabama_power": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.alabamapower.com/"},
    "pge": {"u": "Username or account number", "p": "Password", "u_type": "text", "url": "https://www.pge.com/en_US/home.page"},
    "sce": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.sce.com/"},
    "sdge": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.sdge.com/"},
    "comed": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.comed.com/"},
    "peco": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.peco.com/"},
    "pepco": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.pepco.com/"},
    "bge": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.bge.com/"},
    "consumers_energy": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.consumersenergy.com/"},
    "dte_energy": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.dteenergy.com/"},
    "xcel_energy": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://my.xcelenergy.com/"},
    "ameren": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.ameren.com/"},
    "eversource": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.eversource.com/"},
    "national_grid": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.nationalgridus.com/"},
    "con_ed": {"u": "Username or account number", "p": "Password", "u_type": "text", "url": "https://www.coned.com/"},
    "pseg": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.pseg.com/"},
    "aps": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.aps.com/"},
    "salt_river_project": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.srpnet.com/"},
    "nv_energy": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.nvenergy.com/"},
    "puget_sound_energy": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.pse.com/"},
    "avista": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.avistautilities.com/"},
    "portland_general": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.portlandgeneral.com/"},
    "entergy": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.entergy.com/"},
    "cleco": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.cleco.com/"},
    "central_hudson": {"u": "Username or account number", "p": "Password", "u_type": "text", "url": "https://www.centralhudson.com/"},
    "southern_company": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.southerncompany.com/"},
    # Utilities - Natural Gas
    "atmos_energy": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.atmosenergy.com/"},
    "nicor_gas": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.nicorgas.com/"},
    "peoples_gas": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.peoplesgasdelivery.com/"},
    "piedmont_natural_gas": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.piedmontng.com/"},
    "nw_natural": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.nwnatural.com/"},
    "new_jersey_resources": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.njresources.com/"},
    "south_jersey": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.sjindustries.com/"},
    "laclede_gas": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.lacledegas.com/"},
    # Utilities - Water
    "american_water": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://amwater.com/"},
    "aqua_america": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.aquaamerica.com/"},
    "california_water": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.calwater.com/"},
    # Government & Benefits
    "ssa": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://secure.ssa.gov/RIL/SiView.action"},
    "my_social_security": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.ssa.gov/myaccount/"},
    "irs_online_account": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.irs.gov/payments/your-online-account"},
    "usps": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://reg.usps.com/login"},
    "login_gov": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://secure.login.gov/"},
    "id_me": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://wallet.id.me/users/sign_in"},
    "healthcare_gov": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.healthcare.gov/"},
    "medicare": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.medicare.gov/"},
    "va_gov": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.va.gov/"},
    "usajobs": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.usajobs.gov/"},
    "california_edd": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.edd.ca.gov/"},
    "california_dmv": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.dmv.ca.gov/"},
    "texas_dmv": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.txdmv.gov/"},
    "new_york_dmv": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://dmv.ny.gov/"},
    "florida_dmv": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.flhsmv.gov/"},
    "illinois_dmv": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.ilsos.gov/"},
    "nj_mvc": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.nj.gov/mvc/"},
    "pa_dmv": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.dmv.pa.gov/"},
    "ohio_bmv": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.bmv.ohio.gov/"},
    "ezpass": {"u": "Username or account number", "p": "Password", "u_type": "text", "url": "https://www.e-zpassny.com/"},
    "fas_track": {"u": "Username or account number", "p": "Password", "u_type": "text", "url": "https://www.bayareafastrak.org/"},
    "sunpass": {"u": "Username or account number", "p": "Password", "u_type": "text", "url": "https://www.sunpass.com/"},
    "peach_pass": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.peachpass.com/"},
    "ipass": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.illinoistollway.com/"},
    # Shipping & Logistics
    "ups": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.ups.com/us/en/login"},
    "fedex": {"u": "User ID", "p": "Password", "u_type": "text", "url": "https://www.fedex.com/en-us/home.html"},
    "usps_informed": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://informeddelivery.usps.com/"},
    "dhl": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.dhl.com/us-en/home.html"},
    "stamps_com": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.stamps.com/"},
    "pirateship": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.pirateship.com/login"},
    "shippo": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://goshippo.com/"},
    "shipstation": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.shipstation.com/"},
    "easypost": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.easypost.com/"},
    # Gaming & Apps
    "playstation": {"u": "Sign-In ID (email)", "p": "Password", "u_type": "email", "url": "https://www.playstation.com/en-us/"},
    "xbox": {"u": "Microsoft account email", "p": "Password", "u_type": "email", "url": "https://account.xbox.com/"},
    "nintendo": {"u": "Email address or Nintendo Account ID", "p": "Password", "u_type": "email", "url": "https://accounts.nintendo.com/"},
    "steam": {"u": "Account name", "p": "Password", "u_type": "text", "url": "https://store.steampowered.com/login/"},
    "epic_games": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.epicgames.com/id/login"},
    "battlenet": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://us.battle.net/login/"},
    "ea": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.ea.com/login"},
    "ubisoft": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://account.ubisoft.com/en-US/login"},
    "riot_games": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://auth.riotgames.com/"},
    "roblox": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.roblox.com/login"},
    "minecraft": {"u": "Email address or Minecraft username", "p": "Password", "u_type": "text", "url": "https://www.minecraft.net/en-us/login"},
    "google_play": {"u": "Google account email", "p": "Password", "u_type": "email", "url": "https://play.google.com/"},
    "app_store": {"u": "Apple ID", "p": "Password", "u_type": "email", "url": "https://appleid.apple.com/"},
    "discord": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://discord.com/login"},
    "draftkings": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.draftkings.com/login"},
    "fanduel": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.fanduel.com/login"},
    "betmgm": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://sports.betmgm.com/en/sports/login"},
    "caesars_sportsbook": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.caesarssportsbook.com/"},
    # Gym & Fitness
    "planet_fitness": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.planetfitness.com/member/login"},
    "la_fitness": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.lafitness.com/"},
    "equinox": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.equinox.com/sign-in"},
    "24_hour_fitness": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.24hourfitness.com/"},
    "anytime_fitness": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://my.anytimefitness.com/users/sign_in"},
    "golds_gym": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.goldsgym.com/"},
    "crunch_fitness": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.crunch.com/"},
    "ymca": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://www.ymca.org/"},
    "orangetheory": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.orangetheory.com/en-us/signin"},
    "soulcycle": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.soul-cycle.com/sign-in/"},
    "peloton": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://members.onepeloton.com/login"},
    "classpass": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://classpass.com/login"},
    "life_time": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://my.lifetime.life/"},
    "snap_fitness": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.snapfitness.com/"},
    "strava": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.strava.com/login"},
    "garmin": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://connect.garmin.com/signin/"},
    "fitbit": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.fitbit.com/login"},
    "myfitnesspal": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://www.myfitnesspal.com/user/login"},
    "whoop": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.whoop.com/"},
    "noom": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://web.noom.com/"},
    # Education & Professional
    "coursera": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.coursera.org/login"},
    "udemy": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.udemy.com/join/login-popup/"},
    "linkedin_learning": {"u": "Email address or phone", "p": "Password", "u_type": "email", "url": "https://www.linkedin.com/learning/"},
    "khan_academy": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.khanacademy.org/login"},
    "edx": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://courses.edx.org/login"},
    "skillshare": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.skillshare.com/en/login"},
    "masterclass": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.masterclass.com/auth/login"},
    "pluralsight": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.pluralsight.com/id/"},
    "linkedin": {"u": "Email address or phone", "p": "Password", "u_type": "email", "url": "https://www.linkedin.com/login"},
    "indeed": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://secure.indeed.com/"},
    "glassdoor": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.glassdoor.com/profile/login_input.htm"},
    "monster": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.monster.com/login"},
    "ziprecruiter": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.ziprecruiter.com/login"},
    "handshake": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.joinhandshake.com/login"},
    "chegg": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.chegg.com/login"},
    "quizlet": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://quizlet.com/login"},
    "duolingo": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.duolingo.com/"},
    "babbel": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://my.babbel.com/login"},
    "rosetta_stone": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.rosettastone.com/"},
    "collegeboard": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://account.collegeboard.org/login/signIn"},
    "commonapp": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.commonapp.org/"},
    "fafsa": {"u": "FSA ID username", "p": "Password", "u_type": "text", "url": "https://studentaid.gov/h/apply-for-aid/fafsa"},
    "studentaid": {"u": "FSA ID username or email", "p": "Password", "u_type": "text", "url": "https://studentaid.gov/"},
    "navient": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.navient.com/"},
    "aidvantage": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://aidvantage.com/"},
    "mohela": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.mohela.com/"},
    "nelnet": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.nelnet.com/"},
    "great_lakes": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://mygreatlakes.org/"},
    "canvas": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://canvas.instructure.com/login/"},
    "blackboard": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.blackboard.com/"},
    # Payments & Fintech
    "paypal": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.paypal.com/signin"},
    "venmo": {"u": "Email address, phone number, or username", "p": "Password", "u_type": "text", "url": "https://venmo.com/account/sign-in/"},
    "cashapp": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://cash.app/"},
    "klarna": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://app.klarna.com/login"},
    "afterpay": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.afterpay.com/en-US/login"},
    "affirm": {"u": "Email address or mobile number", "p": "Password", "u_type": "email", "url": "https://www.affirm.com/account/login"},
    "sezzle": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://dashboard.sezzle.com/customer/login"},
    "turbotax": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://myturbotax.intuit.com/"},
    "hrblock": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.hrblock.com/login/"},
    "quickbooks": {"u": "User ID or email", "p": "Password", "u_type": "text", "url": "https://quickbooks.intuit.com/"},
    "credit_karma": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.creditkarma.com/auth/logon"},
    "experian": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.experian.com/consumer-products/member-login.html"},
    "transunion": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.transunion.com/"},
    "equifax": {"u": "Username", "p": "Password", "u_type": "text", "url": "https://www.equifax.com/personal/"},
    "stripe": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://dashboard.stripe.com/login"},
    "square": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://squareup.com/login"},
    # Ride-sharing & Mobility
    "uber": {"u": "Phone number or email", "p": "Password", "u_type": "tel", "url": "https://auth.uber.com/"},
    "lyft": {"u": "Phone number or email", "p": "Password", "u_type": "tel", "url": "https://www.lyft.com/signin"},
    "bird": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.bird.co/"},
    "lime": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.li.me/"},
    "citi_bike": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://citibikenyc.com/account/login"},
    "divvy": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://divvybikes.com/account/login"},
    "blue_bikes": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.bluebikes.com/account/login"},
    "bay_wheels": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.baywheels.com/account/login"},
    # Social & Communication
    "google": {"u": "Gmail address or phone number", "p": "Password", "u_type": "email", "url": "https://accounts.google.com/"},
    "microsoft": {"u": "Email address, phone, or Skype", "p": "Password", "u_type": "email", "url": "https://account.microsoft.com/"},
    "facebook": {"u": "Email address or phone number", "p": "Password", "u_type": "email", "url": "https://www.facebook.com/"},
    "instagram": {"u": "Username, email, or phone", "p": "Password", "u_type": "text", "url": "https://www.instagram.com/accounts/login/"},
    "twitter": {"u": "Phone number, email, or username", "p": "Password", "u_type": "text", "url": "https://twitter.com/login"},
    "tiktok": {"u": "Phone number, email, or username", "p": "Password", "u_type": "text", "url": "https://www.tiktok.com/login"},
    "snapchat": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://accounts.snapchat.com/"},
    "pinterest": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.pinterest.com/login/"},
    "reddit": {"u": "Username or email", "p": "Password", "u_type": "text", "url": "https://www.reddit.com/login/"},
    "zoom": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://zoom.us/signin"},
    "slack": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://slack.com/signin"},
    "dropbox": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.dropbox.com/login"},
    "box": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://account.box.com/login"},
    "icloud": {"u": "Apple ID", "p": "Password", "u_type": "email", "url": "https://www.icloud.com/"},
    "google_drive": {"u": "Google account email", "p": "Password", "u_type": "email", "url": "https://drive.google.com/"},
    # Travel & Experiences
    "amtrak": {"u": "Amtrak Guest Rewards number or email", "p": "Password", "u_type": "text", "url": "https://www.amtrak.com/account/login"},
    "greyhound": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.greyhound.com/en/account"},
    "tripadvisor": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.tripadvisor.com/"},
    "yelp": {"u": "Email address or username", "p": "Password", "u_type": "text", "url": "https://www.yelp.com/login"},
    "open_table": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.opentable.com/login"},
    "resy": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://resy.com/login"},
    "stubhub": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.stubhub.com/login"},
    "ticketmaster": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://auth.ticketmaster.com/"},
    "eventbrite": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.eventbrite.com/signin/"},
    "vivid_seats": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.vividseats.com/login"},
    "seat_geek": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://seatgeek.com/account/login"},
    "recreation_gov": {"u": "Email address", "p": "Password", "u_type": "email", "url": "https://www.recreation.gov/"},
}

# ── Per-site connection notes (2FA warnings, login tips) ──────────────────────
SITE_CONNECT_NOTES = {
    "chase":          {"twofa": True,  "note": "Chase will text or email you a verification code — have your phone ready."},
    "wells_fargo":    {"twofa": True,  "note": "Wells Fargo sends a one-time passcode to your phone or email."},
    "bofa":           {"twofa": True,  "note": "Bank of America may ask you to verify via text, email, or a security question."},
    "capital_one":    {"twofa": True,  "note": "Capital One often requires 2-step verification on new devices."},
    "citi":           {"twofa": True,  "note": "Citi may send a one-time code to your phone or email."},
    "fidelity":       {"twofa": True,  "note": "Fidelity requires 2-factor authentication — have your authenticator app or phone ready."},
    "schwab":         {"twofa": True,  "note": "Schwab may ask for a one-time verification code."},
    "paypal":         {"twofa": True,  "note": "PayPal may ask for a code if this is a new device or browser."},
    "amazon":         {"twofa": False, "note": "Amazon may send a verification code if it doesn't recognize your device."},
    "sfcu":           {"twofa": True,  "note": "Your credit union may require a one-time PIN sent to your phone or email."},
    "pamf":           {"twofa": True,  "note": "MyChart requires 2-factor authentication."},
    "vanguard":       {"twofa": True,  "note": "Vanguard may require a security code sent to your phone or email."},
    "etrade":         {"twofa": True,  "note": "E*TRADE may ask for a security code on new devices."},
    "morgan_stanley": {"twofa": True,  "note": "Morgan Stanley requires multi-factor authentication."},
    "robinhood":      {"twofa": True,  "note": "Robinhood requires 2-factor authentication — have your authenticator app ready."},
    "coinbase":       {"twofa": True,  "note": "Coinbase requires 2-step verification — have your authenticator app or phone ready."},
}

# Failure reason codes → (short label, what to do)
SYNC_FAILURE_MESSAGES = {
    "login_wall":          ("Logged out",      "Log back into this site in Chrome, then click sync."),
    "login_required":      ("Logged out",      "Log back into this site in Chrome, then click sync."),
    "no_data":             ("No data found",   "Visit your account overview page in Chrome, then click sync."),
    "llm_empty":           ("No data found",   "Visit your account overview page in Chrome, then click sync."),
    "low_confidence_only": ("Partial data",    "Visit your full account page in Chrome, then click sync."),
    "stale_date_only":     ("Dates only",      "Visit your account overview page — Mighty only found date fields."),
    "timeout":             ("Timed out",       "The sync took too long. Try clicking sync again."),
    "extension_missing":   ("Extension needed","Install the Mighty Chrome extension, then sync again."),
}

# Direct account URLs — mirrors ACCOUNT_ENTRY in background.js.
# Used to render the "open account" link on each dashboard card.
SITE_ENTRY_URL = {
    "southwest":    "https://www.southwest.com/loyalty/myaccount/",
    "delta":        "https://www.delta.com/myprofile/",
    "united":       "https://www.united.com/en/us/myunited",
    "american_air": "https://www.aa.com/loyalty/home.do",
    "alaska_air":   "https://www.alaskaair.com/account/dashboard",
    "sfcu":         "https://www.sfcu.org/accounts/online-banking",
    "amex":         "https://www.americanexpress.com/en-us/account/",
    "chase":        "https://secure.chase.com/web/auth/dashboard",
    "wells_fargo":  "https://www.wellsfargo.com/change-the-way-you-bank/online-banking/jump/",
    "bofa":         "https://www.bankofamerica.com/myaccounts/brain/render.go",
    "capital_one":  "https://myaccounts.capitalone.com/accountSummary",
    "discover":     "https://portal.discover.com/customer/en/portal/account-home",
    "citi":         "https://online.citi.com/US/JRS/portal/Home.do",
    "paypal":       "https://www.paypal.com/myaccount/summary",
    "fidelity":     "https://digital.fidelity.com/ftgw/digital/portfolio/summary",
    "schwab":       "https://client.schwab.com/app/accounts/#/",
    "vanguard":     "https://personal.vanguard.com/us/myprofile",
    "etrade":       "https://us.etrade.com/etx/pxy/my-account",
    "morgan_stanley": "https://www.morganstanley.com/individual/my-accounts",
    "robinhood":    "https://robinhood.com/account/",
    "coinbase":     "https://www.coinbase.com/dashboard",
    "marriott":     "https://www.marriott.com/loyalty/myAccount/default.mi",
    "hilton":       "https://www.hilton.com/en/hilton-honors/guest/my-account/",
    "hyatt":        "https://www.hyatt.com/en-US/my-account/home",
    "ihg":          "https://www.ihg.com/rewardsclub/content/us/en/member-home",
    "wyndham":      "https://www.wyndhamhotels.com/registry",
    "amazon":       "https://www.amazon.com/gp/css/order-history",
    "target":       "https://www.target.com/account",
    "costco":       "https://www.costco.com/OrderStatusCmd",
    "starbucks":    "https://www.starbucks.com/rewards/",
    "state_farm":   "https://www.statefarm.com/customer-care/my-accounts",
    "pamf":         "https://mychart.pamf.org/MyChart/",
    "ticketmaster": "https://www.ticketmaster.com/member/orders",
    "netflix":      "https://www.netflix.com/YourAccount",
    "hulu":         "https://secure.hulu.com/account",
    "spotify":      "https://www.spotify.com/us/account/overview/",
    "disney_plus":  "https://www.disneyplus.com/identity/account",
    "att":          "https://www.att.com/my/#/",
    "att_wireless": "https://myatt.att.com/exp/myconsumerdashboard/",
    "xfinity":      "https://customer.xfinity.com/#/billing",
    "pa_utilities": "https://utilities.cityofpaloalto.org/",
}

SUPPORTED_SITES = [
    # Banking & Finance
    ("amex",          "American Express",       "💳", "#e8f0fe", "Banking & Finance"),
    ("chase",         "Chase",                  "🏦", "#e3f2fd", "Banking & Finance"),
    ("sfcu",          "Stanford FCU",           "🏦", "#dbeafe", "Banking & Finance"),
    ("wells_fargo",   "Wells Fargo",            "🏦", "#fef3c7", "Banking & Finance"),
    ("bofa",          "Bank of America",        "🏦", "#fee2e2", "Banking & Finance"),
    ("capital_one",   "Capital One",            "💳", "#fce7f3", "Banking & Finance"),
    ("discover",      "Discover",               "💳", "#fff7ed", "Banking & Finance"),
    ("citi",          "Citi",                   "💳", "#ecfdf5", "Banking & Finance"),
    ("paypal",        "PayPal",                 "💰", "#eff6ff", "Banking & Finance"),
    ("fidelity",      "Fidelity",               "📈", "#ecfdf5", "Banking & Finance"),
    ("schwab",        "Charles Schwab",         "📈", "#eff6ff", "Banking & Finance"),
    # Travel
    ("delta",         "Delta",                  "✈️", "#e3f2fd", "Travel"),
    ("united",        "United Airlines",        "✈️", "#eff6ff", "Travel"),
    ("southwest",     "Southwest",              "✈️", "#fef3c7", "Travel"),
    ("american_air",  "American Airlines",      "✈️", "#fce7f3", "Travel"),
    ("alaska_air",    "Alaska Airlines",        "✈️", "#ecfdf5", "Travel"),
    ("hertz",         "Hertz",                  "🚗", "#fff3e0", "Travel"),
    ("marriott",      "Marriott Bonvoy",        "🏨", "#fce8e6", "Travel"),
    ("hilton",        "Hilton Honors",          "🏨", "#e8f5e9", "Travel"),
    ("hyatt",         "Hyatt",                  "🏨", "#f5f3ff", "Travel"),
    ("ihg",           "IHG / Holiday Inn",      "🏨", "#fff7ed", "Travel"),
    ("wyndham",       "Wyndham Rewards",        "🏨", "#fce7f3", "Travel"),
    # Entertainment
    ("disney_plus",   "Disney+",                "🎬", "#e8f0fe", "Entertainment"),
    ("netflix",       "Netflix",                "🎬", "#fee2e2", "Entertainment"),
    ("hulu",          "Hulu",                   "📺", "#ecfdf5", "Entertainment"),
    ("spotify",       "Spotify",                "🎵", "#ecfdf5", "Entertainment"),
    ("max",           "Max",                    "🎬", "#f5f3ff", "Entertainment"),
    ("peacock",       "Peacock",                "🦚", "#fef3c7", "Entertainment"),
    ("paramount_plus","Paramount+",             "🎬", "#eff6ff", "Entertainment"),
    ("ticketmaster",  "Ticketmaster",           "🎟️", "#fce8e6", "Entertainment"),
    # Shopping
    ("amazon",        "Amazon",                 "📦", "#fff8e1", "Shopping"),
    ("target",        "Target",                 "🎯", "#fee2e2", "Shopping"),
    ("walmart",       "Walmart",                "🛒", "#eff6ff", "Shopping"),
    ("costco",        "Costco",                 "🛒", "#eff6ff", "Shopping"),
    # Utilities & Telecom
    ("xfinity",       "Xfinity",               "📡", "#e8f5e9", "Utilities & Telecom"),
    ("pa_utilities",  "Palo Alto Utilities",    "⚡", "#fff3e0", "Utilities & Telecom"),
    ("att",           "AT&T",                   "📱", "#eff6ff", "Utilities & Telecom"),
    ("att_wireless",  "AT&T Wireless",          "📱", "#dbeafe", "Utilities & Telecom"),
    ("verizon",       "Verizon",                "📱", "#fce7f3", "Utilities & Telecom"),
    ("tmobile",       "T-Mobile",               "📱", "#fce7f3", "Utilities & Telecom"),
    # Shopping (loyalty)
    ("starbucks",     "Starbucks",              "☕", "#ecfdf5", "Shopping"),
    ("state_farm",    "State Farm",             "🏠", "#fef3c7", "Insurance"),
    # Health
    ("pamf",          "PAMF MyChart",           "🏥", "#e8f5e9", "Health"),
    ("kaiser",        "Kaiser Permanente",      "🏥", "#ecfdf5", "Health"),
    ("cvs",           "CVS Pharmacy",           "💊", "#fee2e2", "Health"),
    ("walgreens",     "Walgreens",              "💊", "#eff6ff", "Health"),
]


# moved to mighty.scoring


def _system_confidence(
    llm_confidence: float,
    source_type: str,           # "api", "dom", "llm"
    field_key: str,
    source: str,
    uid: str,
) -> float:
    """
    Compute a system-level confidence score combining:
      - LLM confidence (base)
      - Source type bonus (api > dom > llm)
      - Field persistence (seen in N prior syncs)
      - Hint success rate for this field
    Returns 0.0-1.0.
    """
    score = float(llm_confidence or 0.5)

    # Source type bonus
    src_bonus = {"api": 0.10, "dom": 0.05, "llm": 0.0}.get(source_type, 0.0)
    score = min(1.0, score + src_bonus)

    try:
        db = get_db()
        # Field persistence: how many syncs have we seen this field?
        obs_row = db.execute(
            "SELECT seen_count FROM field_observations WHERE user_id=? AND source=? AND field_key=?",
            (uid, source, field_key)
        ).fetchone()
        seen_count = obs_row["seen_count"] if obs_row else 0
        # Each sync we've seen this field adds 0.03 confidence (caps at 0.15 at 5+ syncs)
        persistence_bonus = min(0.15, seen_count * 0.03)
        score = min(1.0, score + persistence_bonus)

        # Hint success rate for this field
        hint_row = db.execute(
            "SELECT confidence, success_count FROM extraction_hints "
            "WHERE site=? AND field_key=? ORDER BY success_count DESC LIMIT 1",
            (source, field_key)
        ).fetchone()
        if hint_row:
            hint_conf = hint_row["confidence"] or 0.0
            hint_success = hint_row["success_count"] or 0
            # Weighted blend: 70% current score, 30% hint confidence (if proven)
            if hint_success >= 3:
                score = 0.7 * score + 0.3 * hint_conf
                score = min(1.0, score)
    except Exception:
        pass

    return round(score, 3)


def _save_discovered_fields(uid: str, source: str, fields: list) -> None:
    """Save AI-discovered fields and update account_data items. Shared by auto and manual discovery."""
    db = get_db()
    cred_row = db.execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    ex = {}
    if cred_row and cred_row["extra_enc"]:
        try: ex = json.loads(decrypt_cred(uid, cred_row["extra_enc"]))
        except Exception: pass

    # Snapshot existing account_data items BEFORE any writes — used for field_history diff below
    _existing_items_snapshot: dict = {}
    try:
        _snap_ad = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source=?", (uid, source)
        ).fetchone()
        if _snap_ad:
            _snap_data = decrypt_account_data(uid, _snap_ad["data_enc"] or "")
            _existing_items_snapshot = {
                item["key"]: item.get("value", "")
                for item in (_snap_data.get("items") or _snap_data.get("ai_items") or [])
                if item.get("key")
            }
    except Exception:
        pass

    # Re-discover REPLACES fields entirely — no merging with stale data.
    # Apply filter first to strip noise (past flights, ticket IDs, etc.)
    fresh = _post_filter_fields(fields)

    # Classify why discovery yielded nothing (for failure tracking)
    _failure_reason: str | None = None
    if not fields:
        _failure_reason = "llm_empty"
    elif not fresh:
        confidences = [f.get("confidence", 0) for f in fields if isinstance(f.get("confidence"), (int, float))]
        if confidences and max(confidences) < 0.70:
            _failure_reason = "low_confidence_only"
        else:
            _failure_reason = "stale_date_only"

    # Dedup by label similarity
    def _n(s): return re.sub(r'[^a-z0-9]', '', s.lower())
    # API-intercepted fields take priority in dedup
    fresh.sort(key=lambda x: 0 if x.get("from_api") else 1)
    seen_labels: set = set(); seen_vals: dict = {}; deduped = []
    for f in fresh:
        val = str(f.get("value", "")).strip(); lbl = _n(f.get("label", ""))
        if any(lbl in sl or sl in lbl for sl in seen_labels): continue
        if val and val not in ("0", "") and val in seen_vals: continue
        seen_labels.add(lbl)
        if val and val not in ("0", ""): seen_vals[val] = f["key"]
        deduped.append(f)

    # Compute system_confidence for each field (calibrated blend of LLM + source + history + hints)
    for f in deduped:
        f["system_confidence"] = _system_confidence(
            llm_confidence=f.get("confidence", 0.5),
            source_type="api" if f.get("from_api") else "llm",
            field_key=f.get("key", ""),
            source=source,
            uid=uid,
        )

    # Confidence-based routing:
    # >=0.85 -> auto-enable; 0.60-0.84 -> candidate for review; <0.60 -> discard
    auto_enabled = []
    candidates_to_insert = []
    for f in deduped:
        sc = f.get("system_confidence") or f.get("confidence") or 0.0
        if sc >= 0.60:
            auto_enabled.append(f["key"])
        elif sc >= 0.40:
            candidates_to_insert.append(f)
        # else: discard silently

    # If no field has confidence metadata (old-style), enable all
    if not auto_enabled and not candidates_to_insert:
        auto_enabled = [f["key"] for f in deduped]

    # Insert candidates into field_candidates table
    if candidates_to_insert:
        now_iso = iso()
        cdb = get_db()
        for cf in candidates_to_insert:
            try:
                cdb.execute("""
                    INSERT INTO field_candidates
                        (user_id, source, field_key, field_label, field_value, confidence, source_snippet, discovered_at, status)
                    VALUES (?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(user_id, source, field_key) DO UPDATE SET
                        field_value=excluded.field_value,
                        confidence=excluded.confidence,
                        source_snippet=excluded.source_snippet,
                        discovered_at=excluded.discovered_at,
                        status=CASE WHEN status='dismissed' THEN 'dismissed' ELSE 'pending' END
                """, (uid, source, cf["key"], cf.get("label", cf["key"]),
                      str(cf.get("value", "")), cf.get("system_confidence", cf.get("confidence", 0)),
                      cf.get("source_snippet", ""), now_iso, "pending"))
            except Exception:
                pass
        cdb.commit()

    ex["enabled_fields"]         = auto_enabled
    ex["review_required_fields"] = []
    ex["discovered_fields"]      = deduped
    ex["discovered_at"]          = iso()
    db.execute(
        "UPDATE account_credentials SET extra_enc=?, updated_at=? WHERE user_id=? AND source=?",
        (encrypt_cred(uid, json.dumps(ex)), iso(), uid, source)
    )
    # Update account_data items
    enabled_set = set(ex["enabled_fields"])
    ai_items = []
    for f in deduped:
        if f.get("key") not in enabled_set:
            continue
        item: dict = {"key": f["key"], "label": f["label"], "value": f.get("value", "–")}
        # Preserve provenance fields when Gemini returned them
        if "confidence" in f:
            item["confidence"] = f["confidence"]
        if "source_snippet" in f:
            item["source_snippet"] = f["source_snippet"]
        if f.get("from_api"):
            item["from_api"] = True
        item["_type"] = classify_benefit(f.get("label",""), str(f.get("value","")), source)
        ai_items.append(item)
    ad = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?", (uid, source)
    ).fetchone()
    if ad and ai_items:
        ad_data = decrypt_account_data(uid, ad["data_enc"] or "")
        ad_data["items"] = ai_items
        db.execute(
            "UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
            (encrypt_account_data(uid, ad_data), uid, source)
        )

    # Field history diff: compare new fields against pre-write snapshot
    try:
        now_iso = iso()
        fh_db = get_db()
        for f in deduped:
            fk = f.get("key")
            nv = str(f.get("value", ""))
            ov = _existing_items_snapshot.get(fk)
            if fk and nv and ov is None:
                # Brand-new field (not seen before) — record as addition (old_value NULL)
                fh_db.execute(
                    "INSERT INTO field_history (user_id, source, field_key, field_label, old_value, new_value, changed_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (uid, source, fk, f.get("label", fk), None, nv, now_iso)
                )
            elif ov is not None and ov != nv:
                # Existing field whose value changed
                fh_db.execute(
                    "INSERT INTO field_history (user_id, source, field_key, field_label, old_value, new_value, changed_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (uid, source, fk, f.get("label", fk), ov, nv, now_iso)
                )
        fh_db.commit()
    except Exception:
        pass

    # Update field_observations: increment seen_count for every field we extracted
    try:
        now_iso = iso()
        fo_db = get_db()
        for f in deduped:
            fk = f.get("key")
            if not fk:
                continue
            fo_db.execute("""
                INSERT INTO field_observations (user_id, source, field_key, first_seen, last_seen, seen_count)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(user_id, source, field_key) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    seen_count = seen_count + 1
            """, (uid, source, fk, now_iso, now_iso))
        fo_db.commit()
    except Exception:
        pass

    # Propagate failure reason to account_data if all fields were dropped
    if _failure_reason:
        _ad_row = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source=?", (uid, source)
        ).fetchone()
        if _ad_row:
            _ad_payload = decrypt_account_data(uid, _ad_row["data_enc"] or "")
            _ad_payload["sync_failure_reason"] = _failure_reason
            db.execute(
                "UPDATE account_data SET data_enc=?, sync_failure_reason=? WHERE user_id=? AND source=?",
                (encrypt_account_data(uid, _ad_payload), _failure_reason, uid, source)
            )
            db.commit()

    # Populate extraction_hints for high-confidence fields
    for _hf in deduped:
        _conf = _hf.get("confidence")
        _snip = _hf.get("source_snippet", "")
        if isinstance(_conf, (int, float)) and _conf >= 0.85 and _snip:
            _trigger = _snip[:60].strip()
            try:
                db.execute(
                    "INSERT INTO extraction_hints "
                    "(site, trigger_phrase, field_key, field_label, neighborhood, confidence, success_count, last_seen) "
                    "VALUES (?,?,?,?,?,?,1,?) "
                    "ON CONFLICT(site, trigger_phrase, field_key) DO UPDATE SET "
                    "success_count = success_count + 1, confidence = excluded.confidence, last_seen = excluded.last_seen",
                    (source, _trigger, _hf["key"], _hf.get("label",""), _snip, _conf, iso())
                )
            except Exception:
                pass
    db.commit()

    # Honour "delete raw capture after extraction" user preference
    try:
        user_pref = get_db().execute(
            "SELECT delete_raw_after_extract FROM users WHERE id=?", (uid,)
        ).fetchone()
        if user_pref and user_pref["delete_raw_after_extract"]:
            ad_row2 = get_db().execute(
                "SELECT data_enc FROM account_data WHERE user_id=? AND source=?", (uid, source)
            ).fetchone()
            if ad_row2:
                ad2 = decrypt_account_data(uid, ad_row2["data_enc"] or "")
                if ad2.get("raw_text"):
                    ad2["raw_text"] = ""
                    get_db().execute(
                        "UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
                        (encrypt_account_data(uid, ad2), uid, source)
                    )
                    get_db().commit()
    except Exception:
        pass


def _promote_pending_candidates(uid: str) -> None:
    """Promote field_candidates with confidence>=0.60 into enabled_fields.

    Runs on every dashboard load (idempotent — candidates already promoted are
    marked 'auto_approved' so subsequent calls are a quick no-op SELECT).
    """
    try:
        db = get_db()
        cands = db.execute(
            "SELECT source, field_key, field_label, field_value, confidence, source_snippet "
            "FROM field_candidates WHERE user_id=? AND status='pending' AND confidence>=0.60",
            (uid,)
        ).fetchall()
        if not cands:
            return
        by_source: dict = {}
        for c in cands:
            by_source.setdefault(c["source"], []).append(c)
        for source, source_cands in by_source.items():
            cred_row = db.execute(
                "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
                (uid, source)
            ).fetchone()
            if not cred_row:
                continue
            try:
                ex = json.loads(decrypt_cred(uid, cred_row["extra_enc"] or "") or "{}")
            except Exception:
                continue
            existing_enabled = set(ex.get("enabled_fields", []))
            disc_by_key = {f["key"]: f for f in ex.get("discovered_fields", [])}
            new_keys = [c["field_key"] for c in source_cands if c["field_key"] not in existing_enabled]
            if not new_keys:
                continue
            for c in source_cands:
                if c["field_key"] not in disc_by_key:
                    disc_by_key[c["field_key"]] = {
                        "key": c["field_key"],
                        "label": c["field_label"],
                        "value": c["field_value"],
                        "confidence": float(c["confidence"]) if c["confidence"] else 0.6,
                    }
            all_enabled = list(existing_enabled | set(new_keys))
            ex["enabled_fields"] = all_enabled
            ex["discovered_fields"] = list(disc_by_key.values())
            db.execute(
                "UPDATE account_credentials SET extra_enc=?, updated_at=? WHERE user_id=? AND source=?",
                (encrypt_cred(uid, json.dumps(ex)), iso(), uid, source)
            )
            enabled_set = set(all_enabled)
            ad = db.execute(
                "SELECT data_enc FROM account_data WHERE user_id=? AND source=?", (uid, source)
            ).fetchone()
            if ad:
                ad_data = decrypt_account_data(uid, ad["data_enc"] or "")
                new_items = [
                    {
                        "key": f["key"],
                        "label": f["label"],
                        "value": f.get("value", ""),
                        "_type": classify_benefit(f.get("label", ""), str(f.get("value", "")), source),
                    }
                    for f in ex["discovered_fields"]
                    if f.get("key") in enabled_set and str(f.get("value", "")).strip()
                ]
                if new_items:
                    ad_data["items"] = new_items
                    db.execute(
                        "UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
                        (encrypt_account_data(uid, ad_data), uid, source)
                    )
        db.commit()
        db.execute(
            "UPDATE field_candidates SET status='auto_approved' "
            "WHERE user_id=? AND status='pending' AND confidence>=0.60",
            (uid,)
        )
        db.commit()
    except Exception as _ppc_err:
        print(f"[Mighty] _promote_pending_candidates error: {_ppc_err}", flush=True)


def _field_config_html(source: str, configured: set, extra_data: dict = None) -> str:
    """Render AI-discovered field checkboxes. New fields are highlighted."""
    if source not in configured:
        return ""
    extra        = extra_data or {}
    discovered   = extra.get("discovered_fields", [])
    enabled      = set(extra.get("enabled_fields", []))
    new_keys     = set(extra.get("new_fields", []))
    src          = he(source)

    if discovered:
        # New-field notification banner
        new_banner = ""
        if new_keys:
            new_labels = [he(f.get("label", f.get("key", ""))) for f in discovered if f.get("key") in new_keys]
            if new_labels:
                label_list = ", ".join(new_labels[:3]) + ("…" if len(new_labels) > 3 else "")
                new_banner = (
                    f'<div style="background:#faf5ff;border:1px solid #e9d5ff;border-radius:8px;'
                    f'padding:8px 12px;margin-bottom:8px;display:flex;align-items:center;gap:8px">'
                    f'<span style="font-size:13px">✨</span>'
                    f'<span style="font-size:12px;color:#7c3aed;flex:1">'
                    f'New fields discovered: <strong>{label_list}</strong></span>'
                    f'<span style="font-size:11px;color:#9ca3af">Save to confirm</span></div>'
                )

        checkboxes = ""
        for f in discovered:
            fkey  = he(f.get("key", ""))
            flbl  = he(f.get("label", ""))
            fval  = he(f.get("value", ""))
            chkd  = "checked" if f.get("key") in enabled else ""
            is_new = f.get("key") in new_keys
            row_style = (
                'display:flex;align-items:center;gap:8px;font-size:12px;padding:5px 0;'
                'cursor:pointer;border-bottom:1px solid #f9f7f5;'
                + ('color:#7c3aed;font-weight:500' if is_new else 'color:#374151')
            )
            new_pill = (
                '<span style="font-size:10px;font-weight:600;padding:1px 6px;border-radius:99px;'
                'background:#f3e8ff;color:#7c3aed;border:1px solid #e9d5ff;margin-left:4px">new</span>'
                if is_new else ''
            )
            checkboxes += (
                f'<label style="{row_style}">'
                f'<input type="checkbox" id="field-{src}-{fkey}" '
                f'data-source="{src}" data-key="{fkey}" {chkd} '
                f'style="width:14px;height:14px;cursor:pointer;flex-shrink:0">'
                f'<span style="flex:1">{flbl}{new_pill}</span>'
                f'<span style="color:#9ca3af;font-size:11px">{fval}</span></label>'
            )
        # Only count new keys that are still present in discovered fields
        _new_present = [f for f in discovered if f.get("key") in new_keys]
        new_count_badge = (
            f'<span style="font-size:11px;font-weight:700;padding:1px 7px;border-radius:99px;'
            f'background:#7c3aed;color:#fff;margin-left:6px">{len(_new_present)} new</span>'
            if _new_present else ""
        )
        return (
            f'<div id="fields-panel-{src}" style="display:none">'
            f'<div style="font-size:13px;font-weight:700;color:#1a1a1a;margin-bottom:10px">'
            f'Field selection{new_count_badge}</div>'
            f'{new_banner}'
            f'{checkboxes}'
            f'<div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">'
            f'<button class="btn-save" style="font-size:12px;padding:6px 14px" '
            f'onclick="saveFieldsModal(\'{src}\')">Save</button>'
            f'<button style="font-size:12px;padding:6px 12px;border-radius:7px;border:1px solid #e5e3df;'
            f'background:#fff;cursor:pointer;color:#6b7280;font-family:inherit" '
            f'onclick="closeFieldModal()">Cancel</button>'
            f'<button style="font-size:12px;padding:6px 12px;border-radius:7px;border:1px solid #fecaca;'
            f'background:#fff;cursor:pointer;color:#dc2626;font-family:inherit;margin-left:auto" '
            f'onclick="clearAndRediscover(\'{src}\')">Clear all &amp; rediscover</button>'
            f'</div></div>'
        )
    else:
        # No fields yet — show a hidden panel with a discover button
        return (
            f'<div id="fields-panel-{src}" style="display:none">'
            f'<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:8px 0">'
            f'<span style="font-size:12px;color:#aeaeb2;font-style:italic">'
            f'Data fields will appear automatically after the first sync</span>'
            f'<button onclick="clearAndRediscover(\'{src}\')" '
            f'style="font-size:11px;padding:4px 10px;border-radius:6px;border:1px solid #e9d5ff;'
            f'background:#faf5ff;color:#7c3aed;cursor:pointer;font-family:inherit;white-space:nowrap">'
            f'Discover now →</button>'
            f'</div></div>'
        )


def _build_dash_modals(configured: set, csrf: str) -> str:
    """Inject the Connect-account modal + field-edit modal into the dashboard page."""
    # ── Build site picker HTML (same logic as credentials page) ──────────────
    modal_categories: dict = {}
    for key, name, icon, color, cat in SUPPORTED_SITES:
        modal_categories.setdefault(cat, []).append((key, name, icon, color))

    modal_sections = ""
    for cat, sites in modal_categories.items():
        site_rows = ""
        for key, name, icon, color in sites:
            already = key in configured
            if already:
                action = (
                    '<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:99px;'
                    'background:rgba(52,211,153,0.1);color:#34d399;border:1px solid rgba(52,211,153,0.25)">Connected</span>'
                )
            else:
                action = (
                    f'<button class="dash-modal-connect-btn" '
                    f'onclick="dashOpenCredForm(\'{he(key)}\',\'{he(name)}\',\'{icon}\',\'{he(color)}\')">'
                    f'Connect</button>'
                )
            site_rows += (
                f'<div class="dash-modal-site-row" data-name="{he(name.lower())}">'
                f'<div style="width:30px;height:30px;border-radius:7px;background:{he(color)};'
                f'display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0">{icon}</div>'
                f'<div style="flex:1;font-size:13px;font-weight:500;color:#1c1917">{he(name)}</div>'
                f'{action}</div>'
            )
        modal_sections += (
            f'<div class="dash-modal-cat-group">'
            f'<div style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;'
            f'color:#9ca3af;margin:16px 0 6px">{he(cat)}</div>'
            f'{site_rows}</div>'
        )

    csrf_esc = csrf.replace("'", "\\'")
    import json as _lhj
    _lh_slim = {k: {'u': v['u'], 'p': v['p'], 't': v['u_type'], 'url': v.get('url','')} for k, v in LOGIN_HINTS.items()}
    _lh_json = _lhj.dumps(_lh_slim)
    _sn_json  = _lhj.dumps({k: {'twofa': v['twofa'], 'note': v['note']} for k, v in SITE_CONNECT_NOTES.items()})
    return f"""
<style>
/* Dashboard modals */
.dash-modal-overlay{{position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:150;display:none;align-items:flex-start;justify-content:center;padding-top:64px;backdrop-filter:blur(2px)}}
.dash-modal-overlay.open{{display:flex}}
.dash-modal{{background:#ffffff;border:1px solid #e8e4de;border-radius:16px;width:100%;max-width:520px;max-height:80vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,0.15)}}
.dash-modal-head{{padding:20px 20px 12px;border-bottom:1px solid #f5f2ed;flex-shrink:0}}
.dash-modal-title{{font-size:16px;font-weight:700;color:#1c1917;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between}}
.dash-modal-search{{width:100%;padding:9px 12px;border-radius:8px;border:1.5px solid #e8e4de;font-size:13px;font-family:inherit;outline:none;color:#1c1917;background:#f5f2ed;transition:border-color .12s}}
.dash-modal-search:focus{{border-color:#6366f1}}
.dash-modal-body{{overflow-y:auto;padding:0 20px 20px;flex:1;min-height:0}}
.dash-modal-site-row{{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid #f5f2ed}}
.dash-modal-site-row:last-child{{border-bottom:none}}
.dash-modal-connect-btn{{padding:5px 12px;border-radius:7px;border:1px solid #e8e4de;background:#f5f2ed;font-size:12px;font-weight:600;color:#6366f1;cursor:pointer;font-family:inherit;flex-shrink:0;transition:all 0.12s}}
.dash-modal-connect-btn:hover{{border-color:#6366f1;background:#eef2ff}}
.dash-field-modal-overlay{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:160;align-items:center;justify-content:center}}
.dash-field-modal-box{{background:#fff;border-radius:16px;width:100%;max-width:520px;max-height:80vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.18);margin:0 16px}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
</style>

<!-- Connect account modal -->
<div class="dash-modal-overlay" id="dash-modal-overlay" onclick="dashOverlayClick(event)">
  <div class="dash-modal">
    <div id="dash-screen-picker" style="display:flex;flex-direction:column;flex:1;min-height:0">
      <div class="dash-modal-head">
        <div class="dash-modal-title">
          <span>Connect an account</span>
          <button onclick="closeDashConnectModal()" style="background:none;border:none;font-size:20px;cursor:pointer;color:#9ca3af;line-height:1;padding:2px 6px">✕</button>
        </div>
        <input class="dash-modal-search" id="dash-modal-search" placeholder="Search sites…"
               autocomplete="off" oninput="dashFilterModal(this.value)">
      </div>
      <div class="dash-modal-body">
        {modal_sections}
        <div id="dash-modal-no-results" style="display:none;text-align:center;padding:32px;color:#9ca3af;font-size:14px">No matching sites.</div>
      </div>
    </div>
    <div id="dash-screen-cred" style="display:none;flex-direction:column;flex:1;min-height:0;overflow-y:auto">
      <div style="padding:16px 20px 12px;border-bottom:1px solid #f5f2ed;display:flex;align-items:center;gap:10px;flex-shrink:0">
        <button onclick="dashBackToPicker()" style="background:none;border:none;color:#6366f1;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit">← Back</button>
        <div id="dash-cred-icon" style="width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:15px"></div>
        <div style="font-size:15px;font-weight:700" id="dash-cred-name"></div>
        <button onclick="closeDashConnectModal()" style="background:none;border:none;font-size:20px;cursor:pointer;color:#9ca3af;margin-left:auto;line-height:1;padding:2px 6px">✕</button>
      </div>
      <div style="padding:20px 20px 24px">
        <div id="dash-cred-hint" style="display:none;font-size:12px;color:#4338ca;background:#eef2ff;border-radius:8px;padding:9px 13px;margin-bottom:14px;line-height:1.5;font-weight:500"></div>
        <div id="dash-twofa-warn" style="display:none;font-size:12px;color:#92400e;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:9px 13px;margin-bottom:14px;line-height:1.5">
          🔐 <strong>2-step verification likely</strong> — <span id="dash-twofa-note"></span>
        </div>
        <div style="margin-bottom:20px">
          <div style="font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:#9ca3af;margin-bottom:10px">How this works</div>
          <div style="display:flex;flex-direction:column;gap:10px">
            <div style="display:flex;align-items:flex-start;gap:10px">
              <div style="width:22px;height:22px;border-radius:50%;background:#059669;color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px">1</div>
              <div style="font-size:13px;color:#374151;line-height:1.5">Click <strong>Open in Chrome</strong> — we'll take you to the <span class="dash-site-name-ref" style="font-weight:600"></span> login page</div>
            </div>
            <div style="display:flex;align-items:flex-start;gap:10px">
              <div style="width:22px;height:22px;border-radius:50%;background:#059669;color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px">2</div>
              <div style="font-size:13px;color:#374151;line-height:1.5"><strong>Log in yourself</strong> <span id="dash-cred-type-note" style="color:#6b7280;font-size:12px"></span> — complete any verification steps if asked</div>
            </div>
            <div style="display:flex;align-items:flex-start;gap:10px">
              <div style="width:22px;height:22px;border-radius:50%;background:#059669;color:#fff;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px">3</div>
              <div style="font-size:13px;color:#374151;line-height:1.5">The Mighty extension <strong>automatically captures your data</strong> — you don't need to do anything else</div>
            </div>
          </div>
        </div>
        <div style="text-align:center;margin-bottom:4px">
          <a id="dash-open-chrome-btn" href="#" target="_blank"
             style="display:inline-block;padding:11px 26px;background:#059669;color:#fff;font-size:14px;font-weight:600;border-radius:9px;text-decoration:none;transition:background 0.15s"
             onmouseenter="this.style.background='#047857'" onmouseleave="this.style.background='#059669'">
            Open in Chrome →
          </a>
        </div>
        <div id="dash-ext-waiting" style="display:none;margin-top:16px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:12px 14px">
          <div style="display:flex;align-items:center;gap:8px;font-size:13px;color:#15803d;font-weight:500;margin-bottom:4px">
            <span style="display:inline-block;width:14px;height:14px;border:2px solid #bbf7d0;border-top-color:#16a34a;border-radius:50%;animation:spin 0.8s linear infinite;flex-shrink:0"></span>
            Waiting for Mighty to detect your session…
          </div>
          <div style="font-size:12px;color:#16a34a;padding-left:22px">Stay on your account page after logging in — usually takes 5–15 seconds</div>
        </div>
        <div id="dash-ext-trouble" style="display:none;margin-top:16px;background:#fef2f2;border:1px solid #fecaca;border-radius:8px;padding:12px 14px;font-size:12px;color:#991b1b">
          <div style="font-weight:600;margin-bottom:8px">⚠ Mighty didn't detect your login. Try these:</div>
          <ol style="margin:0;padding-left:18px;line-height:1.9">
            <li>Make sure the <a href="/extension-setup" target="_blank" style="color:#b91c1c;font-weight:500">Mighty Chrome extension is installed</a></li>
            <li>Navigate to your <strong class="dash-site-name-ref"></strong> account page (not just the home page)</li>
            <li>Make sure you're fully logged in — complete any 2FA prompts</li>
            <li>Stay on the account page for a few seconds</li>
          </ol>
          <button onclick="dashRetryExtPoll()" style="margin-top:10px;padding:6px 14px;background:#991b1b;color:#fff;border:none;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit">Try again</button>
        </div>
      </div>
    </div>
  </div>
</div>

<!-- Field edit modal (dashboard) -->
<div class="dash-field-modal-overlay" id="dash-field-overlay" onclick="if(event.target===this)closeDashFieldModal()">
  <div class="dash-field-modal-box">
    <div style="padding:20px 20px 14px;border-bottom:1px solid #f0ede8;display:flex;align-items:center;justify-content:space-between;flex-shrink:0">
      <div style="font-size:16px;font-weight:700" id="dash-field-title">Edit fields</div>
      <button onclick="closeDashFieldModal()" style="background:none;border:none;font-size:20px;cursor:pointer;color:#9ca3af;line-height:1;padding:2px 6px">✕</button>
    </div>
    <div id="dash-field-body" style="overflow-y:auto;padding:16px 20px 20px;flex:1;min-height:0">
      <div style="text-align:center;padding:24px;color:#9ca3af;font-size:13px">Loading…</div>
    </div>
  </div>
</div>

<script>
var _LOGIN_HINTS = {_lh_json};
var _SITE_NOTES = {_sn_json};
var _DASH_CSRF = '{csrf_esc}';
var _dashModalPollInterval = null;
var _dashCurrentSource = '';
var _DASH_SOURCE_URLS = {{
  southwest:'https://www.southwest.com/loyalty/myaccount/',delta:'https://www.delta.com/myprofile/',
  united:'https://www.united.com/en/us/myaccount/mileageplus',american_air:'https://www.aa.com/aadvantage-program/overview',
  alaska_air:'https://www.alaskaair.com/account/dashboard',amex:'https://www.americanexpress.com/en-us/account/',
  chase:'https://secure.chase.com/web/auth/dashboard',wells_fargo:'https://connect.secure.wellsfargo.com/auth/login/present',
  bofa:'https://www.bankofamerica.com/myaccounts/brain/render.go',capital_one:'https://myaccounts.capitalone.com/accountSummary',
  discover:'https://portal.discover.com/customer/en/portal/account-home',citi:'https://online.citi.com/US/login.do',
  paypal:'https://www.paypal.com/myaccount/summary',fidelity:'https://digital.fidelity.com/ftgw/digital/portfolio/summary',
  marriott:'https://www.marriott.com/loyalty/myAccount/default.mi',hilton:'https://www.hilton.com/en/hilton-honors/guest/my-account/',
  hyatt:'https://www.hyatt.com/en-US/my-account/home',ihg:'https://www.ihg.com/rewardsclub/content/us/en/member-home',
  amazon:'https://www.amazon.com/gp/css/order-history',target:'https://www.target.com/account',
  starbucks:'https://www.starbucks.com/rewards/',netflix:'https://www.netflix.com/YourAccount',
  spotify:'https://www.spotify.com/us/account/overview/',att:'https://www.att.com/my/#/',
  verizon:'https://www.verizon.com/myverizon/',tmobile:'https://account.t-mobile.com/overview',
  hertz:'https://www.hertz.com/rentacar/member/profile/myprofile'
}};
// Merge login URLs from LOGIN_HINTS (covers 613 sites)
(function(){{
  Object.entries(_LOGIN_HINTS).forEach(function(e){{
    if(e[1].url && !_DASH_SOURCE_URLS[e[0]]) _DASH_SOURCE_URLS[e[0]] = e[1].url;
  }});
}})();

function openDashConnectModal() {{
  document.getElementById('dash-modal-overlay').classList.add('open');
  document.getElementById('dash-screen-picker').style.display = 'flex';
  document.getElementById('dash-screen-cred').style.display = 'none';
  setTimeout(function(){{ var s=document.getElementById('dash-modal-search'); if(s) s.focus(); }}, 50);
}}
function closeDashConnectModal() {{
  document.getElementById('dash-modal-overlay').classList.remove('open');
  if (_dashModalPollInterval) {{ clearInterval(_dashModalPollInterval); _dashModalPollInterval = null; }}
}}
function dashOverlayClick(e) {{
  if (e.target === document.getElementById('dash-modal-overlay')) closeDashConnectModal();
}}
function dashFilterModal(q) {{
  q = (q || '').toLowerCase().trim();
  var anyVisible = false;
  document.querySelectorAll('.dash-modal-site-row').forEach(function(row) {{
    var show = !q || (row.dataset.name || '').includes(q);
    row.style.display = show ? '' : 'none';
    if (show) anyVisible = true;
  }});
  document.querySelectorAll('.dash-modal-cat-group').forEach(function(grp) {{
    var vis = Array.from(grp.querySelectorAll('.dash-modal-site-row')).some(r => r.style.display !== 'none');
    grp.style.display = vis ? '' : 'none';
  }});
  var nr = document.getElementById('dash-modal-no-results');
  if (nr) nr.style.display = (q && !anyVisible) ? '' : 'none';
}}
function dashBackToPicker() {{
  document.getElementById('dash-screen-picker').style.display = 'flex';
  document.getElementById('dash-screen-cred').style.display = 'none';
  if (_dashModalPollInterval) {{ clearInterval(_dashModalPollInterval); _dashModalPollInterval = null; }}
}}
function dashOpenCredForm(key, name, icon, color) {{
  _dashCurrentSource = key;
  if (_dashModalPollInterval) {{ clearInterval(_dashModalPollInterval); _dashModalPollInterval = null; }}
  document.getElementById('dash-cred-name').textContent = name;
  var ic = document.getElementById('dash-cred-icon');
  ic.textContent = icon; ic.style.background = color;

  // Set site name everywhere it appears
  document.querySelectorAll('.dash-site-name-ref').forEach(function(el) {{ el.textContent = name; }});

  // Credential hint pill
  var h = _LOGIN_HINTS[key];
  var hintEl = document.getElementById('dash-cred-hint');
  if (hintEl) {{
    var idLabel = h ? h.u : 'email or username';
    var pwLabel = h ? h.p.toLowerCase() : 'password';
    hintEl.textContent = '🔑 You’ll log in with your ' + idLabel.toLowerCase() + ' and ' + pwLabel + '.';
    hintEl.style.display = 'block';
  }}

  // Credential type sub-note in step 2
  var typeNote = document.getElementById('dash-cred-type-note');
  if (typeNote) {{
    typeNote.textContent = h ? '(use your ' + h.u.toLowerCase() + ')' : '';
  }}

  // 2FA warning
  var siteNote = _SITE_NOTES[key];
  var twofaEl = document.getElementById('dash-twofa-warn');
  var twofaNoteEl = document.getElementById('dash-twofa-note');
  if (twofaEl) {{
    if (siteNote && siteNote.twofa) {{
      if (twofaNoteEl) twofaNoteEl.textContent = siteNote.note || 'Have your phone or authenticator app ready.';
      twofaEl.style.display = 'block';
    }} else {{
      twofaEl.style.display = 'none';
    }}
  }}

  // Reset status panels
  var waiting = document.getElementById('dash-ext-waiting');
  var trouble = document.getElementById('dash-ext-trouble');
  if (waiting) waiting.style.display = 'none';
  if (trouble) trouble.style.display = 'none';

  // Wire up Open in Chrome button
  var openBtn = document.getElementById('dash-open-chrome-btn');
  var siteUrl = _DASH_SOURCE_URLS[key] || 'https://google.com/search?q=' + encodeURIComponent(name + ' login');
  if (openBtn) {{
    openBtn.href = siteUrl;
    openBtn.onclick = function() {{ _dashStartExtPoll(key); }};
    openBtn.style.display = '';
  }}

  // If extension not present, skip straight to trouble panel
  if (!_extPresent) {{
    if (openBtn) openBtn.style.display = 'none';
    if (trouble) trouble.style.display = 'block';
  }}

  document.getElementById('dash-screen-picker').style.display = 'none';
  document.getElementById('dash-screen-cred').style.display = 'flex';
}}
function _dashStartExtPoll(source) {{
  var waiting = document.getElementById('dash-ext-waiting');
  var trouble = document.getElementById('dash-ext-trouble');
  if (waiting) waiting.style.display = 'block';
  if (trouble) trouble.style.display = 'none';
  fetch('/credentials/register', {{
    method:'POST', headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: _DASH_CSRF, source: source}})
  }});
  var attempts = 0;
  _dashModalPollInterval = setInterval(function() {{
    attempts++;
    fetch('/api/extension/poll/' + source).then(function(r){{return r.json();}}).then(function(d){{
      if (d.captured) {{
        clearInterval(_dashModalPollInterval); _dashModalPollInterval = null;
        if (waiting) waiting.innerHTML = (
          '<div style="display:flex;align-items:center;gap:8px;font-size:13px;color:#15803d;font-weight:600">' +
          '<span>✓</span> Connected! Loading your data…</div>'
        );
        setTimeout(function() {{ closeDashConnectModal(); location.reload(); }}, 1200);
      }}
    }}).catch(function(){{}});
    if (attempts >= 20) {{
      clearInterval(_dashModalPollInterval); _dashModalPollInterval = null;
      if (waiting) waiting.style.display = 'none';
      if (trouble) trouble.style.display = 'block';
    }}
  }}, 3000);
}}
function dashRetryExtPoll() {{
  document.getElementById('dash-ext-trouble').style.display = 'none';
  _dashStartExtPoll(_dashCurrentSource);
}}

/* Field edit modal */
function openDashFieldModal(source, displayName) {{
  document.getElementById('dash-field-title').textContent = (displayName || source) + ' — Edit fields';
  document.getElementById('dash-field-body').innerHTML = '<div style="text-align:center;padding:24px;color:#9ca3af;font-size:13px">Loading…</div>';
  document.getElementById('dash-field-overlay').style.display = 'flex';
  fetch('/api/fields-panel/' + encodeURIComponent(source)).then(function(r){{return r.json();}}).then(function(d){{
    if (d.html) {{
      document.getElementById('dash-field-body').innerHTML = d.html;
    }} else {{
      document.getElementById('dash-field-body').innerHTML = '<p style="font-size:13px;color:#9ca3af">No fields yet. Sync this account first.</p>';
    }}
  }}).catch(function(){{
    document.getElementById('dash-field-body').innerHTML = '<p style="font-size:13px;color:#ef4444">Error loading fields.</p>';
  }});
}}
function closeDashFieldModal() {{
  document.getElementById('dash-field-overlay').style.display = 'none';
}}
function saveDashFields(source) {{
  var body = document.getElementById('dash-field-body');
  var boxes = body.querySelectorAll('[data-source="' + source + '"]');
  var enabled = Array.from(boxes).filter(b => b.checked).map(b => b.dataset.key);
  fetch('/credentials/fields', {{
    method:'POST', headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: _DASH_CSRF, source: source, enabled_fields: JSON.stringify(enabled)}})
  }}).then(r => r.json()).then(function(d) {{
    if (d.ok) {{
      closeDashFieldModal();
      var t = document.getElementById('mighty-toast');
      if (t) {{ t.textContent = 'Saved ✓'; t.classList.add('show'); setTimeout(function(){{t.classList.remove('show');}}, 2000); }}
    }}
  }});
}}
function clearAndRediscoverDash(source) {{
  if (!confirm('Clear all fields and rediscover from the latest sync data?')) return;
  fetch('/credentials/fields/reset/' + source, {{
    method:'POST', headers:{{'X-CSRF-Token': _DASH_CSRF}}
  }}).then(r => r.json()).then(function(d) {{
    if (!d.ok) {{ alert('Reset failed'); return; }}
    fetch('/credentials/discover/' + source, {{
      method:'POST', headers:{{'X-CSRF-Token': _DASH_CSRF}}
    }}).then(r => r.json()).then(function(d2) {{
      closeDashFieldModal();
      var t = document.getElementById('mighty-toast');
      if (t) {{ t.textContent = d2.ok ? 'Fields rediscovered ✓' : 'Reset done — fields will appear after next sync'; t.classList.add('show'); setTimeout(function(){{t.classList.remove('show');}}, 2500); }}
    }});
  }});
}}
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') {{
    var oo = document.getElementById('onboarding-overlay');
    if (oo && oo.style.display !== 'none') dismissOnboarding();
    var co = document.getElementById('dash-modal-overlay');
    if (co && co.classList.contains('open')) closeDashConnectModal();
    var fo = document.getElementById('dash-field-overlay');
    if (fo && fo.style.display !== 'none') closeDashFieldModal();
    var co2 = document.getElementById('dash-cred-overlay');
    if (co2 && co2.style.display !== 'none') closeCredModal();
    var ap = document.getElementById('arch-inline-panel');
    if (ap && ap.style.display !== 'none') ap.style.display = 'none';
    closeBenefitDrawer();
  }}
}});
</script>

<script>
function archiveBenefit(src, lbl, btn) {{
  var row = btn.parentElement;
  // Walk up to find the actual row (has border-bottom in its style)
  while (row && row !== document.body && !row.style.borderBottom) row = row.parentElement;
  if (!row || row === document.body) row = btn.parentElement;
  // Optimistic UI: fade and remove the row immediately
  row.style.transition = 'opacity 0.25s';
  row.style.opacity = '0';
  setTimeout(function() {{
    row.remove();
    if (!window._ARCHIVED_BENEFITS) window._ARCHIVED_BENEFITS = [];
    _ARCHIVED_BENEFITS.push({{source: src, label: lbl}});
    _updateArchivedFooter();
  }}, 250);
  // Persist to server
  fetch('/api/benefits/archive', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: _DASH_CSRF, source: src, label: lbl}})
  }});
}}
function _updateArchivedFooter() {{
  var count = (_ARCHIVED_BENEFITS || []).length;
  var wrap = document.getElementById('arch-footer-wrap');
  var btn  = document.getElementById('arch-toggle-btn');
  if (btn) btn.textContent = '↩ ' + count + ' archived';
  if (wrap) wrap.style.display = count > 0 ? 'block' : 'none';
  // Close the panel if nothing left
  if (count === 0) {{
    var panel = document.getElementById('arch-inline-panel');
    if (panel) panel.style.display = 'none';
  }}
}}
function toggleArchivedPanel() {{
  var panel = document.getElementById('arch-inline-panel');
  if (!panel) return;
  var isOpen = panel.style.display !== 'none';
  if (isOpen) {{ panel.style.display = 'none'; return; }}
  var items = window._ARCHIVED_BENEFITS || [];
  var list = document.getElementById('arch-inline-list');
  if (!list || !items.length) return;
  list.innerHTML = items.map(function(item, i) {{
    return '<div style="display:flex;align-items:center;justify-content:space-between;'
         + 'padding:7px 0;border-bottom:1px solid #f5f2ed" id="arch-item-' + i + '">'
         + '<div style="font-size:13px;color:#6b7280">' + item.label
         + ' <span style="color:#d1d5db">\xb7</span> <span style="font-size:12px;color:#9ca3af">' + item.source + '</span></div>'
         + '<button onclick="restoreArchivedBenefit(' + i + ')" '
         + 'style="font-size:11px;color:#6366f1;background:none;border:none;cursor:pointer;'
         + 'text-decoration:underline;padding:0;flex-shrink:0;margin-left:12px">Restore</button>'
         + '</div>';
  }}).join('');
  panel.style.display = 'block';
}}
function restoreArchivedBenefit(idx) {{
  var item = (_ARCHIVED_BENEFITS || [])[idx];
  if (!item) return;
  fetch('/api/benefits/unarchive', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: _DASH_CSRF, source: item.source, label: item.label}})
  }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
    if (d.ok) {{
      _ARCHIVED_BENEFITS.splice(idx, 1);
      var panel = document.getElementById('arch-inline-panel');
      if (panel) panel.style.display = 'none';
      location.reload();
    }}
  }});
}}
</script>

<!-- Credential edit modal -->
<div id="dash-cred-overlay" onclick="if(event.target===this)closeCredModal()"
     style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.35);z-index:310;align-items:center;justify-content:center"></div>
<div id="dash-cred-modal"
     style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);
            background:#fff;border-radius:12px;box-shadow:0 8px 40px rgba(0,0,0,0.18);
            width:min(400px,92vw);padding:24px;z-index:311">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px">
    <h3 id="dash-cred-title" style="font-size:15px;font-weight:700;color:#1c1917;margin:0">Edit login</h3>
    <button onclick="closeCredModal()" style="background:none;border:none;cursor:pointer;color:#9ca3af;font-size:18px;line-height:1;padding:2px 6px">&times;</button>
  </div>
  <div style="margin-bottom:12px">
    <label for-field="dash-cred-username" style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:4px">Username or email</label>
    <input id="dash-cred-username" type="text" autocomplete="off" placeholder="Username or email"
           style="width:100%;box-sizing:border-box;border:1px solid #e5e7eb;border-radius:7px;padding:9px 11px;font-size:14px;color:#1c1917;outline:none">
  </div>
  <div style="margin-bottom:12px">
    <label for-field="dash-cred-password" style="font-size:11px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:4px">Password</label>
    <input id="dash-cred-password" type="password" autocomplete="new-password" placeholder="Leave blank to keep existing"
           style="width:100%;box-sizing:border-box;border:1px solid #e5e7eb;border-radius:7px;padding:9px 11px;font-size:14px;color:#1c1917;outline:none">
  </div>
  <details style="margin-bottom:16px">
    <summary style="font-size:12px;color:#9ca3af;cursor:pointer;user-select:none">Authenticator app 2FA (optional)</summary>
    <input id="dash-cred-totp" type="text" placeholder="TOTP secret key"
           style="width:100%;box-sizing:border-box;border:1px solid #e5e7eb;border-radius:7px;padding:9px 11px;font-size:14px;margin-top:8px">
    <div style="font-size:11px;color:#9ca3af;margin-top:4px">Disable &amp; re-enable 2FA on the site, choose "Enter key manually", paste the string here.</div>
  </details>
  <input id="dash-cred-source" type="hidden">
  <div style="display:flex;gap:8px;justify-content:flex-end">
    <button onclick="closeCredModal()" style="padding:8px 16px;border:1px solid #e5e7eb;border-radius:7px;background:#fff;font-size:13px;color:#6b7280;cursor:pointer">Cancel</button>
    <button onclick="saveCredModal()" style="padding:8px 18px;border:none;border-radius:7px;background:#6366f1;color:#fff;font-size:13px;font-weight:600;cursor:pointer">Save &amp; Sync</button>
  </div>
  <div id="dash-cred-error" style="display:none;margin-top:10px;font-size:12px;color:#ef4444"></div>
</div>
<script>
function openCredModal(src, displayName) {{
  document.getElementById('dash-cred-title').textContent = (displayName || src) + ' — Edit login';
  document.getElementById('dash-cred-source').value = src;
  document.getElementById('dash-cred-password').value = '';
  document.getElementById('dash-cred-totp').value = '';
  document.getElementById('dash-cred-error').style.display = 'none';
  // Apply per-site credential labels from LOGIN_HINTS
  var hints = (typeof _LOGIN_HINTS !== 'undefined' && _LOGIN_HINTS[src]) || {{}};
  var uLabel = hints.u || 'Username or email';
  var pLabel = hints.p || 'Password';
  var uType  = hints.u_type || 'text';
  document.querySelector('label[for-field="dash-cred-username"]').textContent = uLabel.toUpperCase();
  document.querySelector('label[for-field="dash-cred-password"]').textContent = pLabel.toUpperCase();
  var uInput = document.getElementById('dash-cred-username');
  uInput.type = uType;
  uInput.placeholder = uLabel;
  uInput.value = 'Loading…';
  document.getElementById('dash-cred-overlay').style.display = 'flex';
  document.getElementById('dash-cred-modal').style.display = 'block';
  fetch('/api/credentials/' + encodeURIComponent(src) + '/username')
    .then(function(r) {{ return r.json(); }})
    .then(function(d) {{
      document.getElementById('dash-cred-username').value = d.username || '';
    }}).catch(function() {{
      document.getElementById('dash-cred-username').value = '';
    }});
}}
function closeCredModal() {{
  document.getElementById('dash-cred-overlay').style.display = 'none';
  document.getElementById('dash-cred-modal').style.display = 'none';
}}
function saveCredModal() {{
  try {{
    var src  = document.getElementById('dash-cred-source').value;
    var user = document.getElementById('dash-cred-username').value.trim();
    var pass = document.getElementById('dash-cred-password').value;
    var totp = document.getElementById('dash-cred-totp').value.trim();
    var err  = document.getElementById('dash-cred-error');
    var saveBtn = document.querySelector('#dash-cred-modal button:last-of-type');
    if (err) err.style.display = 'none';
    if (!user) {{
      if (err) {{ err.textContent = 'Username is required.'; err.style.display = 'block'; }}
      return;
    }}
    if (saveBtn) {{ saveBtn.textContent = 'Saving…'; saveBtn.disabled = true; }}
    var params = {{_csrf: _DASH_CSRF, source: src, username: user}};
    if (pass) params.password = pass;          // only send if re-entered; blank = keep existing
    if (totp) params.totp_secret = totp;
    var body = new URLSearchParams(params);
    fetch('/credentials/save', {{
      method: 'POST',
      headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
      body: body
    }})
      .then(function(r) {{
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      }})
      .then(function(d) {{
        if (!d.ok) {{
          if (saveBtn) {{ saveBtn.textContent = 'Save & Sync'; saveBtn.disabled = false; }}
          if (err) {{ err.textContent = d.error || 'Save failed.'; err.style.display = 'block'; }}
          return;
        }}
        closeCredModal();
        if (saveBtn) {{ saveBtn.textContent = 'Save & Sync'; saveBtn.disabled = false; }}
        var t = document.getElementById('mighty-toast');
        if (t) {{ t.textContent = 'Credentials saved — syncing…'; t.classList.add('show'); setTimeout(function(){{t.classList.remove('show');}}, 3000); }}
        setTimeout(function(){{ location.reload(); }}, 2000);
      }})
      .catch(function(e) {{
        if (saveBtn) {{ saveBtn.textContent = 'Save & Sync'; saveBtn.disabled = false; }}
        if (err) {{ err.textContent = 'Error: ' + (e.message || 'Network error'); err.style.display = 'block'; }}
      }});
  }} catch(e) {{
    var err2 = document.getElementById('dash-cred-error');
    if (err2) {{ err2.textContent = 'JS error: ' + e.message; err2.style.display = 'block'; }}
  }}
}}
</script>

<!-- Benefit detail drawer -->
<div id="benefit-drawer-overlay" onclick="closeBenefitDrawer()"
     style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.3);z-index:300"></div>
<div id="benefit-drawer"
     style="position:fixed;top:0;right:0;height:100vh;width:min(380px,100vw);background:#fff;
            z-index:301;box-shadow:-4px 0 32px rgba(0,0,0,0.12);display:flex;flex-direction:column;
            transform:translateX(110%);transition:transform 0.25s cubic-bezier(.4,0,.2,1)">
  <div style="padding:20px 20px 14px;border-bottom:1px solid #f5f2ed;
              display:flex;align-items:center;justify-content:space-between;flex-shrink:0">
    <span id="bd-icon" style="font-size:28px;line-height:1"></span>
    <button onclick="closeBenefitDrawer()"
            style="background:none;border:none;font-size:22px;color:#9ca3af;cursor:pointer;
                   padding:4px 8px;line-height:1;margin-left:auto">✕</button>
  </div>
  <div style="padding:20px 20px 24px;overflow-y:auto;flex:1">
    <div id="bd-label" style="font-size:20px;font-weight:700;color:#111;line-height:1.3;margin-bottom:4px"></div>
    <div id="bd-account" style="font-size:13px;color:#6b7280;margin-bottom:20px"></div>

    <div id="bd-expiry-row" style="display:none;background:#fffbeb;border:1px solid #fde68a;
         border-radius:8px;padding:12px 14px;margin-bottom:14px">
      <div style="font-size:10px;font-weight:700;color:#9ca3af;text-transform:uppercase;
                  letter-spacing:.06em;margin-bottom:4px">Expires</div>
      <div id="bd-expiry" style="font-size:16px;font-weight:700;color:#d97706"></div>
    </div>

    <div id="bd-value-row" style="display:none;background:#f9fafb;border:1px solid #e5e7eb;
         border-radius:8px;padding:12px 14px;margin-bottom:14px">
      <div style="font-size:10px;font-weight:700;color:#9ca3af;text-transform:uppercase;
                  letter-spacing:.06em;margin-bottom:4px">Value</div>
      <div id="bd-value" style="font-size:16px;font-weight:700;color:#111"></div>
    </div>

    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:14px;margin-bottom:14px">
      <div style="font-size:10px;font-weight:700;color:#9ca3af;text-transform:uppercase;
                  letter-spacing:.06em;margin-bottom:6px">Why this matters</div>
      <div id="bd-why" style="font-size:13px;color:#374151;line-height:1.65"></div>
    </div>

    <!-- Partnership / card drawer extras -->
    <div id="bd-detail-row" style="display:none;background:#f9fafb;border:1px solid #e5e7eb;
         border-radius:8px;padding:12px 14px;margin-bottom:14px">
      <div style="font-size:10px;font-weight:700;color:#9ca3af;text-transform:uppercase;
                  letter-spacing:.06em;margin-bottom:4px">What it is</div>
      <div id="bd-detail" style="font-size:13px;color:#374151;line-height:1.5"></div>
    </div>

    <div id="bd-card-benefits-row" style="display:none;background:#f9fafb;border:1px solid #e5e7eb;
         border-radius:8px;padding:12px 14px;margin-bottom:14px">
      <div style="font-size:10px;font-weight:700;color:#9ca3af;text-transform:uppercase;
                  letter-spacing:.06em;margin-bottom:6px">Published benefits</div>
      <ul id="bd-card-benefits" style="margin:0;padding:0 0 0 14px;list-style:disc"></ul>
    </div>

    <div id="bd-source-row" style="display:none;background:#eff6ff;border:1px solid #bfdbfe;
         border-radius:8px;padding:12px 14px;margin-bottom:14px">
      <div style="font-size:10px;font-weight:700;color:#9ca3af;text-transform:uppercase;
                  letter-spacing:.06em;margin-bottom:4px">Where Mighty found this</div>
      <div id="bd-source-text" style="font-size:13px;color:#1e40af;line-height:1.5"></div>
    </div>

    <div id="bd-verified-row" style="display:none;border-top:1px solid #f5f2ed;padding-top:12px">
      <div style="font-size:10px;color:#d1d5db;line-height:1.5">
        <span id="bd-verified-text"></span>
      </div>
    </div>

    <!-- Correction UI: "Wrong type?" -->
    <div id="bd-correction-row" style="display:none;margin-top:16px;border-top:1px solid #f5f2ed;padding-top:14px">
      <div style="font-size:11px;font-weight:600;color:#9ca3af;text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">
        Wrong category?
      </div>
      <div id="bd-corrected-badge" style="display:none;font-size:11px;color:#7c3aed;margin-bottom:8px">
        ✓ You corrected this
      </div>
      <div style="display:flex;gap:8px;align-items:center">
        <select id="bd-type-select"
          style="flex:1;font-size:12px;padding:6px 8px;border:1px solid #e5e7eb;border-radius:6px;
                 background:#f9fafb;color:#374151;cursor:pointer">
          <option value="">— select correct type —</option>
          <option value="certificate">Certificate / Award</option>
          <option value="travel_credit">Travel Credit</option>
          <option value="cash_credit">Cash Credit</option>
          <option value="points_balance">Points / Miles Balance</option>
          <option value="elite_status">Elite Status</option>
          <option value="membership">Membership / Access</option>
          <option value="progress_toward">Progress Toward Goal</option>
          <option value="upcoming_event">Upcoming Event</option>
          <option value="reservation">Reservation / Booking</option>
          <option value="payment_due">Payment Due</option>
          <option value="renewal">Renewal / Annual Fee</option>
          <option value="partner_benefit">Partner Benefit</option>
          <option value="expiry_date">Expiry Date Only</option>
          <option value="other">Other</option>
        </select>
        <button id="bd-correct-btn" onclick="submitBenefitCorrection()"
          style="font-size:12px;padding:6px 12px;background:#6c47ff;color:#fff;border:none;
                 border-radius:6px;cursor:pointer;font-weight:600;white-space:nowrap">
          Save
        </button>
      </div>
      <div id="bd-correct-status" style="font-size:11px;color:#6b7280;margin-top:6px;display:none"></div>
    </div>
  </div>
</div>

<script>
var _BD_WHY = {{
  cert: 'Can be redeemed for a companion or award ticket — book a flight to your destination and apply it at checkout.',
  upgrade: 'Apply to an eligible flight for a complimentary cabin upgrade, subject to inventory availability.',
  credit: 'Offsets travel or dining purchases made on your card. Check eligible categories before it expires.',
  status: 'Unlocks elite benefits including priority boarding, lounge access, complimentary upgrades, and bonus miles on every flight.',
  points: 'Redeemable for flights, hotels, gift cards, or merchandise through your loyalty program portal.',
  night: 'Redeem for a free hotel night at any eligible property in the program.',
  dflt: 'A benefit in your account — check the account site for redemption instructions.'
}};
function _bdWhy(label, icon) {{
  var l = (label||'').toLowerCase();
  if (icon==='🎫' || l.includes('cert') || l.includes('companion') || l.includes('free night')) return _BD_WHY.cert;
  if (icon==='⬆' || l.includes('upgrade')) return _BD_WHY.upgrade;
  if (l.includes('credit') || l.includes('voucher') || l.includes('ecredit')) return _BD_WHY.credit;
  if (l.includes('status') || l.includes('elite') || l.includes('medallion') ||
      l.includes('gold') || l.includes('platinum') || l.includes('diamond')) return _BD_WHY.status;
  if (l.includes('miles') || l.includes('points') || l.includes('rewards')) return _BD_WHY.points;
  if (l.includes('night') || l.includes('hotel')) return _BD_WHY.night;
  return _BD_WHY.dflt;
}}
function openBenefitDrawer(el) {{
  var raw = el && el.dataset && el.dataset.benefit;
  if (!raw) return;
  try {{ var d = JSON.parse(raw); }} catch(e) {{ return; }}
  document.getElementById('bd-icon').textContent = d.icon || '•';
  document.getElementById('bd-label').textContent = d.label || '';
  document.getElementById('bd-account').textContent = d.account || '';
  // Expiry
  var eRow = document.getElementById('bd-expiry-row');
  var eEl  = document.getElementById('bd-expiry');
  if (d.expDays != null && d.expDays >= 0) {{
    var ed = new Date(); ed.setDate(ed.getDate() + Math.round(d.expDays));
    var mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][ed.getMonth()];
    var expStr = mo + ' ' + ed.getDate() + ', ' + ed.getFullYear();
    if (d.expDays <= 30) {{
      eEl.innerHTML = expStr + ' <span style="font-size:13px;color:#dc2626">(in ' + d.expDays + ' days)</span>';
      eEl.style.color = '#dc2626';
      eRow.style.background = '#fff5f5';
      eRow.style.borderColor = '#fca5a5';
    }} else {{
      eEl.textContent = expStr;
      eEl.style.color = '#d97706';
      eRow.style.background = '#fffbeb';
      eRow.style.borderColor = '#fde68a';
    }}
    eRow.style.display = 'block';
  }} else {{ eRow.style.display = 'none'; }}
  // Value
  var vRow = document.getElementById('bd-value-row');
  var vEl  = document.getElementById('bd-value');
  var _skipV = {{'available':1,'active':1,'yes':1,'enabled':1,'valid':1,'earned':1,'':1}};
  if (d.value && !_skipV[(d.value||'').toLowerCase().trim()]) {{
    vEl.textContent = d.value;
    vRow.style.display = 'block';
  }} else {{ vRow.style.display = 'none'; }}
  // Why
  document.getElementById('bd-why').textContent = _bdWhy(d.label, d.icon);

  // ── Partnership / card extras ──────────────────────────────────────────
  // "What it is" (detail text for derived/partnership items)
  var detRow = document.getElementById('bd-detail-row');
  var detEl  = document.getElementById('bd-detail');
  if (d.detail) {{
    detEl.textContent = d.detail;
    detRow.style.display = 'block';
  }} else {{ detRow.style.display = 'none'; }}

  // "Published benefits" (for card recommendations)
  var cbRow = document.getElementById('bd-card-benefits-row');
  var cbEl  = document.getElementById('bd-card-benefits');
  if (d.card_benefits && d.card_benefits.length) {{
    cbEl.innerHTML = d.card_benefits.map(function(b) {{
      return '<li style="font-size:12px;color:#374151;margin-bottom:3px;line-height:1.45">' +
             b.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') + '</li>';
    }}).join('');
    cbRow.style.display = 'block';
  }} else {{ cbRow.style.display = 'none'; }}

  // "Where Mighty found this"
  var srcRow  = document.getElementById('bd-source-row');
  var srcText = document.getElementById('bd-source-text');
  var sourceMsg = '';
  if (d.derived && d.via_account) {{
    sourceMsg = 'Derived from your ' + d.via_account + ' account';
    if (d.via_status) sourceMsg += ' (' + d.via_status + ')';
  }} else if (d.card_issuer) {{
    sourceMsg = 'Published card benefit from ' + d.card_issuer;
    if (d.trigger_account) sourceMsg += ' · triggered by your ' + d.trigger_account + ' account';
  }} else if (d.account) {{
    sourceMsg = 'Found in your ' + d.account + ' account';
  }}
  if (sourceMsg) {{
    srcText.textContent = sourceMsg;
    srcRow.style.display = 'block';
  }} else {{ srcRow.style.display = 'none'; }}

  // "Last verified / data freshness"
  var verRow  = document.getElementById('bd-verified-row');
  var verText = document.getElementById('bd-verified-text');
  var verParts = [];
  if (d.synced_ago) verParts.push('Last synced ' + d.synced_ago);
  if (d.confidence)  verParts.push(d.confidence + ' confidence');
  if (d.last_verified) {{
    verParts.push('Partnership verified ' + d.last_verified);
    if (d.source_url) verParts.push('Confirm current terms: ' + d.source_url);
  }}
  if (verParts.length) {{
    verText.textContent = verParts.join(' · ');
    verRow.style.display = 'block';
  }} else {{ verRow.style.display = 'none'; }}

  // Correction UI
  var corrRow  = document.getElementById('bd-correction-row');
  var corrBadge = document.getElementById('bd-corrected-badge');
  var corrSel  = document.getElementById('bd-type-select');
  var corrStat = document.getElementById('bd-correct-status');
  if (d.field_key) {{
    corrRow.style.display = 'block';
    corrRow.dataset.fieldKey = d.field_key;
    corrRow.dataset.btype    = d.btype || '';
    corrSel.value = d.btype || '';
    corrBadge.style.display = d.corrected ? 'block' : 'none';
    corrStat.style.display = 'none';
  }} else {{
    corrRow.style.display = 'none';
  }}

  // Open
  document.getElementById('benefit-drawer').style.transform = 'translateX(0)';
  document.getElementById('benefit-drawer-overlay').style.display = 'block';
  document.body.style.overflow = 'hidden';
}}
function submitBenefitCorrection() {{
  var corrRow = document.getElementById('bd-correction-row');
  var sel     = document.getElementById('bd-type-select');
  var stat    = document.getElementById('bd-correct-status');
  var badge   = document.getElementById('bd-corrected-badge');
  var btn     = document.getElementById('bd-correct-btn');
  var fieldKey = corrRow.dataset.fieldKey;
  var newType  = sel.value;
  if (!fieldKey || !newType) return;
  btn.disabled = true;
  btn.textContent = '…';
  fetch('/api/benefits/correct', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/json', 'X-CSRF-Token': (typeof CSRF !== 'undefined' ? CSRF : '')}},
    body: JSON.stringify({{field_key: fieldKey, corrected_type: newType}})
  }})
  .then(function(r) {{ return r.json(); }})
  .then(function(j) {{
    if (j.ok) {{
      stat.textContent = 'Saved — page will refresh to apply.';
      stat.style.color = '#059669';
      stat.style.display = 'block';
      badge.style.display = 'block';
      btn.textContent = '✓';
      setTimeout(function() {{ location.reload(); }}, 1200);
    }} else {{
      stat.textContent = j.error || 'Error saving correction.';
      stat.style.color = '#dc2626';
      stat.style.display = 'block';
      btn.disabled = false;
      btn.textContent = 'Save';
    }}
  }})
  .catch(function() {{
    stat.textContent = 'Network error — try again.';
    stat.style.color = '#dc2626';
    stat.style.display = 'block';
    btn.disabled = false;
    btn.textContent = 'Save';
  }});
}}
function closeBenefitDrawer() {{
  var dr = document.getElementById('benefit-drawer');
  var ov = document.getElementById('benefit-drawer-overlay');
  if (dr) dr.style.transform = 'translateX(110%)';
  if (ov) ov.style.display = 'none';
  document.body.style.overflow = '';
}}
</script>"""


def _build_credentials_page(user, configured: set, extra_by_source: dict = None, synced_at_by_source: dict = None) -> str:
    """Generate the credentials management page HTML."""
    extra_by_source = extra_by_source or {}
    synced_at_by_source = synced_at_by_source or {}
    csrf = get_csrf_token()

    # ── Connected account cards (main page) ──────────────────────────────────
    connected_cards_html = ""
    for key, name, icon, color, cat in SUPPORTED_SITES:
        if key not in configured:
            continue
        remove_btn = (
            '<button class="btn-remove" onclick="if(confirm(\'Disconnect this account? This will remove saved credentials.\'))removeCred(\''
            + he(key) + '\',\'' + he(name) + '\')" style="cursor:pointer">Disconnect</button>'
        )
        _cred_synced = synced_at_by_source.get(key, "")
        _cred_sync_label = (
            f'<div style="font-size:11px;color:#8892a4;margin-top:2px" data-synced="{he(_cred_synced)}">Synced {_fmt_sync(_cred_synced)}</div>'
            if _cred_synced else
            '<div style="font-size:11px;color:#9ca3af;margin-top:2px">Not yet synced</div>'
        )
        connected_cards_html += f"""
<div class="cred-card" id="card-{he(key)}">
  <div style="display:flex;align-items:center;gap:12px">
    <div style="width:36px;height:36px;border-radius:9px;background:{he(color)};display:flex;align-items:center;justify-content:center;font-size:17px;flex-shrink:0">{icon}</div>
    <div style="flex:1">
      <div style="font-size:14px;font-weight:600;color:#1c1917">{he(name)}</div>
      {_cred_sync_label}
    </div>
    <button class="btn-toggle" onclick="openFieldModal('{he(key)}')" id="btn-fields-{he(key)}">Edit fields</button>
    <button class="btn-toggle" onclick="toggleForm('{he(key)}')" id="btn-{he(key)}"
            style="color:#8892a4;font-weight:500">Edit login</button>
    {remove_btn}
  </div>
  <div class="cred-form" id="form-{he(key)}" style="display:none;margin-top:14px">
    <input type="text" name="username" placeholder="Username or email" autocomplete="off" id="u-{he(key)}">
    <input type="password" name="password" placeholder="Password" autocomplete="new-password" id="p-{he(key)}">
    <details style="margin-top:8px">
      <summary style="font-size:12px;color:#8892a4;cursor:pointer;user-select:none">Authenticator app 2FA (optional)</summary>
      <input type="text" name="totp" placeholder="TOTP secret key" style="margin-top:6px" id="t-{he(key)}">
      <div style="font-size:11px;color:#9ca3af;margin-top:4px">Disable &amp; re-enable 2FA on the site, choose "Enter key manually", paste the string here.</div>
    </details>
    <button class="btn-save" onclick="saveCred('{he(key)}', '{he(name)}')">Save & Sync</button>
  </div>
  {_field_config_html(key, configured, extra_by_source.get(key, {}))}
</div>"""

    if not connected_cards_html:
        connected_cards_html = """
<div style="text-align:center;padding:56px 24px;color:#9ca3af">
  <div style="font-size:36px;margin-bottom:14px">🔗</div>
  <div style="font-size:15px;font-weight:500;color:#6b7280;margin-bottom:6px">No accounts connected yet</div>
  <div style="font-size:13px">Click <strong>Connect account</strong> above to get started.</div>
</div>"""

    # ── Modal site picker ────────────────────────────────────────────────────
    modal_categories: dict = {}
    for key, name, icon, color, cat in SUPPORTED_SITES:
        modal_categories.setdefault(cat, []).append((key, name, icon, color))

    modal_sections = ""
    for cat, sites in modal_categories.items():
        site_rows = ""
        for key, name, icon, color in sites:
            already = key in configured
            if already:
                action = f'<span style="font-size:11px;font-weight:600;padding:2px 8px;border-radius:99px;background:rgba(52,211,153,0.1);color:#34d399;border:1px solid rgba(52,211,153,0.25)">Connected</span>'
            else:
                action = (
                    f'<button class="modal-connect-btn" '
                    f'onclick="openCredForm(\'{he(key)}\',\'{he(name)}\',\'{icon}\',\'{he(color)}\')">'
                    f'Connect</button>'
                )
            site_rows += f"""
<div class="modal-site-row" data-name="{he(name.lower())}">
  <div style="width:30px;height:30px;border-radius:7px;background:{he(color)};display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0">{icon}</div>
  <div style="flex:1;font-size:13px;font-weight:500;color:#1c1917">{he(name)}</div>
  {action}
</div>"""
        modal_sections += f"""
<div class="modal-cat-group">
  <div style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:#9ca3af;margin:16px 0 6px">{he(cat)}</div>
  {site_rows}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="color-scheme" content="light">
<title>Connected Accounts — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{height:100%;overflow:hidden;font-family:'Inter',sans-serif}}
body{{display:flex;flex-direction:row;background:#eae5de;color:#1c1917;-webkit-font-smoothing:antialiased}}
/* ── Sidebar ── */
.sidebar{{width:48px;flex-shrink:0;background:#0a0c12;border-right:1px solid rgba(255,255,255,0.06);display:flex;flex-direction:column;height:100vh;overflow:hidden;align-items:center}}
.sidebar-header{{padding:14px 0 10px;border-bottom:1px solid rgba(255,255,255,0.06);width:100%;display:flex;justify-content:center}}
.sidebar-logo{{display:flex;align-items:center;justify-content:center;text-decoration:none}}
.sidebar-logo:hover{{text-decoration:none}}
.sidebar-logo-img{{width:26px;height:26px;border-radius:7px;object-fit:cover}}
.sidebar-nav{{flex:1;padding:8px 0;display:flex;flex-direction:column;align-items:center;gap:2px;overflow-y:auto;width:100%}}
.sidebar-link{{width:36px;height:36px;display:flex;align-items:center;justify-content:center;border-radius:8px;color:#3d4560;text-decoration:none;transition:background 0.1s,color 0.1s}}
.sidebar-link:hover{{background:rgba(255,255,255,0.07);color:#c4cde0;text-decoration:none}}
.sidebar-link svg{{flex-shrink:0}}
.sidebar-link-active{{background:rgba(129,140,248,0.15);color:#818cf8 !important}}
.sidebar-footer{{padding:10px 0 12px;border-top:1px solid rgba(255,255,255,0.06);width:100%;display:flex;justify-content:center}}
.sidebar-avatar{{width:28px;height:28px;border-radius:50%;background:linear-gradient(135deg,#6366f1,#818cf8);display:flex;align-items:center;justify-content:center;font-size:11px;font-weight:700;color:#fff;flex-shrink:0;border:none;cursor:pointer;font-family:inherit;position:relative}}
.sidebar-tip{{position:fixed;left:54px;background:#1a1d2e;color:#e2e8f0;font-size:12px;font-weight:500;padding:5px 10px;border-radius:7px;white-space:nowrap;pointer-events:none;opacity:0;transition:opacity 0.1s;z-index:999;border:1px solid rgba(255,255,255,0.08)}}
.sidebar-link:hover .sidebar-tip,.sidebar-logo:hover .sidebar-tip,.sidebar-avatar:hover .sidebar-tip{{opacity:1}}
/* ── Main ── */
.main-content{{flex:1;min-width:0;height:100vh;overflow-y:auto}}
.page{{max-width:660px;margin:0 auto;padding:32px 28px}}
.page-header{{display:flex;align-items:center;justify-content:space-between;margin-bottom:24px}}
h1{{font-size:20px;font-weight:700;color:#1c1917}}
.btn-connect-new{{padding:8px 16px;border-radius:8px;background:#6366f1;color:#fff;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;display:flex;align-items:center;gap:6px;transition:background 0.12s}}
.btn-connect-new:hover{{background:#4f46e5}}
/* ── Cred cards ── */
.cred-card{{background:#ffffff;border:1px solid #e8e4de;border-radius:12px;padding:16px 18px;margin-bottom:10px;transition:border-color 0.15s;box-shadow:0 1px 2px rgba(0,0,0,0.05),0 4px 16px rgba(0,0,0,0.06)}}
.cred-card:hover{{border-color:#d0ccc5}}
.cred-form input{{width:100%;padding:9px 12px;border:1.5px solid #e8e4de;border-radius:8px;font-size:13px;font-family:inherit;outline:none;margin-top:8px;transition:border-color 0.12s;background:#ffffff;color:#1c1917}}
.cred-form input:focus{{border-color:#6366f1}}
.cred-form input::placeholder{{color:#c0bbb5}}
.cred-form details{{margin-top:8px;border:1px solid #e8e4de;border-radius:8px;overflow:hidden}}
.cred-form details summary{{font-size:12px;color:#6b7280;cursor:pointer;user-select:none;padding:8px 12px;background:#f5f2ed;list-style:none;display:flex;align-items:center;justify-content:space-between}}
.cred-form details summary::after{{content:'＋';font-size:14px;color:#9ca3af}}
.cred-form details[open] summary::after{{content:'－'}}
.cred-form details input{{margin:0;border:none;border-top:1px solid #e8e4de;border-radius:0}}
.btn-toggle{{padding:5px 12px;border-radius:7px;border:1px solid #e8e4de;background:#f5f2ed;font-size:12px;font-weight:600;color:#6366f1;cursor:pointer;font-family:inherit;transition:all 0.12s}}
.btn-toggle:hover{{border-color:#6366f1;background:#eef2ff}}
.btn-remove{{padding:5px 10px;border-radius:7px;border:1px solid rgba(220,38,38,0.25);background:transparent;font-size:12px;color:#dc2626;cursor:pointer;font-family:inherit;transition:all 0.12s}}
.btn-remove:hover{{background:rgba(220,38,38,0.06);border-color:#dc2626}}
.btn-save{{margin-top:12px;padding:9px 18px;border-radius:8px;background:#6366f1;color:#fff;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;transition:background 0.12s}}
.btn-save:hover{{background:#4f46e5}}
/* ── Modal ── */
.modal-overlay{{position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:50;display:none;align-items:flex-start;justify-content:center;padding-top:64px;backdrop-filter:blur(2px)}}
.modal-overlay.open{{display:flex}}
.modal{{background:#ffffff;border:1px solid #e8e4de;border-radius:16px;width:100%;max-width:520px;max-height:80vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,0.15)}}
.modal-head{{padding:20px 20px 12px;border-bottom:1px solid #f5f2ed;flex-shrink:0}}
.modal-title{{font-size:16px;font-weight:700;color:#1c1917;margin-bottom:12px;display:flex;align-items:center;justify-content:space-between}}
.modal-close{{background:none;border:none;font-size:20px;cursor:pointer;color:#9ca3af;line-height:1;padding:2px 6px;transition:color 0.12s}}
.modal-close:hover{{color:#1c1917}}
.modal-search{{width:100%;padding:9px 12px;border-radius:8px;border:1.5px solid #e8e4de;font-size:13px;font-family:inherit;outline:none;color:#1c1917;background:#f5f2ed;transition:border-color .12s}}
.modal-search:focus{{border-color:#6366f1}}
.modal-search::placeholder{{color:#c0bbb5}}
.modal-body{{overflow-y:auto;padding:0 20px 20px;flex:1;min-height:0}}
.modal-cat-group .modal-cat-label{{font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;color:#9ca3af;margin:16px 0 6px}}
.modal-site-row{{display:flex;align-items:center;gap:10px;padding:9px 0;border-bottom:1px solid #f5f2ed}}
.modal-site-row:last-child{{border-bottom:none}}
.modal-connect-btn{{padding:5px 12px;border-radius:7px;border:1px solid #e8e4de;background:#f5f2ed;font-size:12px;font-weight:600;color:#6366f1;cursor:pointer;font-family:inherit;flex-shrink:0;transition:all 0.12s}}
.modal-connect-btn:hover{{border-color:#6366f1;background:#eef2ff}}
.modal-cred-screen{{display:none;flex-direction:column;flex:1;min-height:0}}
.modal-cred-screen.active{{display:flex}}
.modal-cred-head{{padding:20px 20px 16px;border-bottom:1px solid #f5f2ed;flex-shrink:0;display:flex;align-items:center;gap:12px}}
.modal-back-btn{{background:none;border:none;font-size:14px;cursor:pointer;color:#6366f1;font-family:inherit;font-weight:600;padding:0;transition:color 0.12s}}
.modal-back-btn:hover{{color:#4f46e5}}
.modal-cred-body{{padding:20px;overflow-y:auto;flex:1;min-height:0}}
.modal-cred-body input{{width:100%;padding:9px 12px;border:1.5px solid #e8e4de;border-radius:8px;font-size:13px;font-family:inherit;outline:none;margin-top:10px;transition:border-color .12s;background:#ffffff;color:#1c1917}}
.modal-cred-body input:focus{{border-color:#6366f1}}
.modal-cred-body input::placeholder{{color:#c0bbb5}}
.toast{{position:fixed;bottom:24px;right:24px;background:#1c1917;color:#f5f2ed;border:1px solid #333;padding:10px 18px;border-radius:9px;font-size:13px;opacity:0;transition:opacity 0.2s;pointer-events:none;z-index:200;box-shadow:0 4px 20px rgba(0,0,0,0.15)}}
.toast.show{{opacity:1}}
@media(max-width:768px){{html,body{{height:auto;overflow:auto}}.sidebar{{display:none}}.main-content{{height:auto;overflow:visible;padding-left:0!important}}.nav-hamburger{{display:flex!important}}.topbar-search{{flex:1;min-width:0}}}}
</style>
</head>
<body>
{_sidebar_html('accounts', user["email"], csrf)}

<div class="main-content">
<div style="display:none;align-items:center;gap:10px;padding:12px 16px;border-bottom:0.5px solid rgba(0,0,0,0.07);background:#eee9e2;position:sticky;top:0;z-index:2" id="mobile-topbar-accounts">
  <button class="nav-hamburger" onclick="openMobileDrawer()" aria-label="Open menu" style="display:none">
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
  </button>
  <span style="font-size:15px;font-weight:700;color:#1c1917">Accounts</span>
</div>
<script>(function(){{var t=document.getElementById('mobile-topbar-accounts');if(t&&window.innerWidth<=768)t.style.display='flex';}})();</script>
<div class="page">
  <div class="page-header">
    <h1>Connected accounts</h1>
    <button class="btn-connect-new" onclick="openModal()">+ Connect account</button>
  </div>
  {connected_cards_html}
  <div style="text-align:center;padding:16px 0 8px;font-size:12px;color:#9ca3af">Changes take effect on next sync.</div>
</div>


<!-- Add account modal -->
<div class="modal-overlay" id="modal-overlay" onclick="overlayClick(event)">
  <div class="modal" id="modal-box">

    <!-- Screen 1: site picker -->
    <div id="screen-picker" style="display:flex;flex-direction:column;flex:1;min-height:0">
      <div class="modal-head">
        <div class="modal-title">
          <span>Connect an account</span>
          <button class="modal-close" onclick="closeModal()">✕</button>
        </div>
        <input class="modal-search" id="modal-search" placeholder="Search sites…"
               autocomplete="off" oninput="filterModal(this.value)">
      </div>
      <div class="modal-body" id="modal-sites">
        {modal_sections}
        <div id="modal-no-results" style="display:none;text-align:center;padding:32px;color:#9ca3af;font-size:14px">No matching sites.</div>
      </div>
    </div>

    <!-- Screen 2: extension-first connect -->
    <div id="screen-cred" style="display:none;flex-direction:column;flex:1;min-height:0">
      <div class="modal-cred-head">
        <button class="modal-back-btn" onclick="backToPicker()">← Back</button>
        <div id="modal-cred-icon" style="width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:15px"></div>
        <div style="font-size:15px;font-weight:700" id="modal-cred-name"></div>
        <button class="modal-close" style="margin-left:auto" onclick="closeModal()">✕</button>
      </div>
      <div class="modal-cred-body">
        <div style="text-align:center;padding:8px 0 20px">
          <div style="font-size:32px;margin-bottom:12px" id="modal-ext-icon-lg"></div>
          <div style="font-size:14px;font-weight:600;color:#1c1917;margin-bottom:8px">Connect via Chrome</div>
          <div style="font-size:13px;color:#6b7280;line-height:1.6;margin-bottom:20px">
            Make sure you're <strong>logged into <span id="modal-ext-site-name"></span></strong> in Chrome,
            then click the button below. The Mighty extension will capture your account data automatically.
          </div>
          <a id="modal-open-chrome-btn"
             href="#" target="_blank"
             style="display:inline-block;padding:11px 22px;background:#059669;color:#fff;font-size:14px;font-weight:600;border-radius:9px;text-decoration:none;transition:background 0.15s"
             onmouseenter="this.style.background='#047857'" onmouseleave="this.style.background='#059669'">
            Open in Chrome →
          </a>
          <div id="modal-ext-waiting" style="display:none;margin-top:20px;text-align:center">
            <div style="display:flex;align-items:center;justify-content:center;gap:8px;font-size:13px;color:#6b7280">
              <span style="display:inline-block;width:14px;height:14px;border:2px solid #d1fae5;border-top-color:#059669;border-radius:50%;animation:spin 0.8s linear infinite"></span>
              Waiting for Mighty extension to detect your session…
            </div>
            <div style="font-size:11px;color:#9ca3af;margin-top:6px">This usually takes 5–15 seconds after you log in</div>
          </div>
          <div id="modal-ext-no-ext" style="display:none;margin-top:16px;background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px 12px;font-size:12px;color:#92400e;text-align:left">
            💡 <strong>Extension not installed?</strong> Visit <a href="/extension-setup" target="_blank" style="color:#b45309">Settings → Setup Chrome Extension</a> first.
          </div>
        </div>
      </div>
    </div>

    <!-- Screen 3: sync progress -->
    <div id="screen-progress" style="display:none;flex-direction:column;flex:1;align-items:center;justify-content:center;padding:40px 32px;gap:16px">
      <div style="font-size:15px;font-weight:700;color:#1c1917;margin-bottom:8px" id="sync-progress-title">Connecting account…</div>
      <div style="width:100%;max-width:300px;display:flex;flex-direction:column;gap:12px">
        <div id="sync-step-1" data-label="Saving credentials" style="font-size:13px;color:#9ca3af;display:flex;align-items:center">
          <span style="margin-right:6px">○</span>Saving credentials
        </div>
        <div id="sync-step-2" data-label="Syncing account" style="font-size:13px;color:#9ca3af;display:flex;align-items:center">
          <span style="margin-right:6px">○</span>Syncing account
        </div>
        <div id="sync-step-3" data-label="Discovering fields" style="font-size:13px;color:#9ca3af;display:flex;align-items:center">
          <span style="margin-right:6px">○</span>Discovering fields
        </div>
      </div>
      <div id="sync-progress-error" style="display:none;margin-top:8px;font-size:12px;color:#ef4444;text-align:center"></div>
    </div>

  </div>
</div>

<!-- Field editing modal -->
<div id="field-modal-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:60;align-items:center;justify-content:center" onclick="fieldOverlayClick(event)">
  <div id="field-modal-box" style="background:#fff;border-radius:16px;width:100%;max-width:520px;max-height:80vh;display:flex;flex-direction:column;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.18);margin:0 16px">
    <div style="padding:20px 20px 14px;border-bottom:1px solid #f0ede8;display:flex;align-items:center;justify-content:space-between;flex-shrink:0">
      <div style="font-size:16px;font-weight:700" id="field-modal-title">Edit fields</div>
      <button onclick="closeFieldModal()" style="background:none;border:none;font-size:20px;cursor:pointer;color:#9ca3af;line-height:1;padding:2px 6px">&#x2715;</button>
    </div>
    <div id="field-modal-body" style="overflow-y:auto;padding:16px 20px 20px;flex:1;min-height:0"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
var CSRF = '{csrf}';
var _modalKey = '';
var _fieldModalSource = '';

/* ── Modal open/close ─────────────────────────────── */
function openModal() {{
  document.getElementById('modal-overlay').classList.add('open');
  document.getElementById('modal-search').focus();
  showPicker();
}}
function closeModal() {{
  document.getElementById('modal-overlay').classList.remove('open');
  // Stop any running poll
  if (_modalPollInterval) {{ clearInterval(_modalPollInterval); _modalPollInterval = null; }}
  if (_modalPollTimeout)  {{ clearTimeout(_modalPollTimeout);   _modalPollTimeout  = null; }}
  // Reset progress screen for next use
  ['sync-step-1','sync-step-2','sync-step-3'].forEach(function(id) {{
    var el = document.getElementById(id);
    if (el) {{ el.innerHTML = '<span style="margin-right:6px">○</span>' + el.dataset.label; el.style.color='#9ca3af'; el.style.fontWeight='500'; }}
  }});
  var err = document.getElementById('sync-progress-error');
  if (err) {{ err.style.display = 'none'; err.textContent = ''; }}
  document.getElementById('screen-progress').style.display = 'none';
}}
function overlayClick(e) {{
  if (e.target === document.getElementById('modal-overlay')) closeModal();
}}
document.addEventListener('keydown', function(e) {{
  if (e.key === 'Escape') {{
    var mo = document.getElementById('modal-overlay');
    if (mo && mo.classList.contains('open')) closeModal();
    var fo = document.getElementById('field-modal-overlay');
    if (fo && fo.style.display !== 'none') fo.style.display = 'none';
  }}
}});
function showPicker() {{
  document.getElementById('screen-picker').style.display = 'flex';
  document.getElementById('screen-cred').style.display = 'none';
}}
function backToPicker() {{
  showPicker();
  _modalKey = '';
}}

/* ── Source → account URL map for "Open in Chrome →" ── */
var _SOURCE_URLS = {{
  southwest:    'https://www.southwest.com/loyalty/myaccount/',
  delta:        'https://www.delta.com/myprofile/',
  united:       'https://www.united.com/en/us/myaccount/mileageplus',
  american_air: 'https://www.aa.com/aadvantage-program/overview',
  alaska_air:   'https://www.alaskaair.com/account/dashboard',
  amex:         'https://www.americanexpress.com/en-us/account/',
  chase:        'https://secure.chase.com/web/auth/dashboard',
  wells_fargo:  'https://connect.secure.wellsfargo.com/auth/login/present',
  bofa:         'https://www.bankofamerica.com/myaccounts/brain/render.go',
  capital_one:  'https://myaccounts.capitalone.com/accountSummary',
  discover:     'https://portal.discover.com/customer/en/portal/account-home',
  citi:         'https://online.citi.com/US/login.do',
  paypal:       'https://www.paypal.com/myaccount/summary',
  fidelity:     'https://digital.fidelity.com/ftgw/digital/portfolio/summary',
  schwab:       'https://client.schwab.com/app/accounts/#/',
  marriott:     'https://www.marriott.com/loyalty/myAccount/default.mi',
  hilton:       'https://www.hilton.com/en/hilton-honors/guest/my-account/',
  hyatt:        'https://www.hyatt.com/en-US/my-account/home',
  ihg:          'https://www.ihg.com/rewardsclub/content/us/en/member-home',
  wyndham:      'https://www.wyndhamhotels.com/registry',
  amazon:       'https://www.amazon.com/gp/css/order-history',
  target:       'https://www.target.com/account',
  costco:       'https://www.costco.com/OrderStatusCmd',
  starbucks:    'https://www.starbucks.com/rewards/',
  state_farm:   'https://www.statefarm.com/customer-care/sign-in-to-my-account',
  pamf:         'https://mychart.pamf.org/MyChart/',
  ticketmaster: 'https://www.ticketmaster.com/member/orders',
  netflix:      'https://www.netflix.com/YourAccount',
  hulu:         'https://secure.hulu.com/account',
  spotify:      'https://www.spotify.com/us/account/overview/',
  disney_plus:  'https://www.disneyplus.com/identity/account',
  att:          'https://www.att.com/my/#/',
  att_wireless: 'https://myatt.att.com/exp/myconsumerdashboard/',
  verizon:      'https://www.verizon.com/myverizon/',
  tmobile:      'https://account.t-mobile.com/overview',
  xfinity:      'https://customer.xfinity.com/#/billing',
  hertz:        'https://www.hertz.com/rentacar/member/profile/myprofile',
  cvs:          'https://www.cvs.com/account/login.jsp',
  walgreens:    'https://www.walgreens.com/myaccount/mywalgreenssummary.jsp',
  sfcu:         'https://www.sfcu.org/accounts/online-banking',
}};

var _modalPollInterval = null;
var _modalPollTimeout  = null;

/* ── Open extension-connect screen for a site ─────── */
function openCredForm(key, name, icon, color) {{
  _modalKey = key;

  // Stop any existing poll
  if (_modalPollInterval) {{ clearInterval(_modalPollInterval); _modalPollInterval = null; }}
  if (_modalPollTimeout)  {{ clearTimeout(_modalPollTimeout);   _modalPollTimeout  = null; }}

  // Header
  document.getElementById('modal-cred-name').textContent = name;
  var ic = document.getElementById('modal-cred-icon');
  ic.textContent = icon; ic.style.background = color;

  // Large icon + site name in body
  var lg = document.getElementById('modal-ext-icon-lg');
  if (lg) {{ lg.textContent = icon; }}
  var sn = document.getElementById('modal-ext-site-name');
  if (sn) {{ sn.textContent = name; }}

  // Set "Open in Chrome →" href
  var openBtn = document.getElementById('modal-open-chrome-btn');
  var siteUrl = _SOURCE_URLS[key] || 'https://google.com/search?q=' + encodeURIComponent(name + ' login');
  if (openBtn) {{
    openBtn.href = siteUrl;
    openBtn.onclick = function() {{ _startExtPoll(key); }};
  }}

  // Hide waiting states
  var waiting = document.getElementById('modal-ext-waiting');
  var noExt   = document.getElementById('modal-ext-no-ext');
  if (waiting) waiting.style.display = 'none';
  if (noExt)   noExt.style.display   = 'none';

  document.getElementById('screen-picker').style.display = 'none';
  document.getElementById('screen-cred').style.display   = 'flex';
}}

function _startExtPoll(source) {{
  var waiting = document.getElementById('modal-ext-waiting');
  var noExt   = document.getElementById('modal-ext-no-ext');
  if (waiting) waiting.style.display = 'block';

  // Register placeholder so it shows on dashboard even before data arrives
  fetch('/credentials/register', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF, source: source}})
  }});

  var attempts = 0;
  var maxAttempts = 40; // 2 minutes

  _modalPollInterval = setInterval(function() {{
    attempts++;
    fetch('/api/extension/poll/' + source)
      .then(function(r) {{ return r.json(); }})
      .then(function(d) {{
        if (d.captured) {{
          clearInterval(_modalPollInterval); _modalPollInterval = null;
          _setStep_ext('done');
          setTimeout(function() {{ closeModal(); location.reload(); }}, 800);
        }}
      }}).catch(function() {{}});

    if (attempts >= maxAttempts) {{
      clearInterval(_modalPollInterval); _modalPollInterval = null;
      if (waiting) waiting.style.display = 'none';
      if (noExt)   noExt.style.display   = 'block';
    }}
  }}, 3000);
}}

function _setStep_ext(state) {{
  var waiting = document.getElementById('modal-ext-waiting');
  if (state === 'done' && waiting) {{
    waiting.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;gap:8px;font-size:13px;color:#16a34a"><span>✓</span> Account connected!</div>';
  }}
}}

/* ── Save from modal ──────────────────────────────── */
function _setStep(step, state) {{
  // state: 'active' | 'done' | 'error'
  var el = document.getElementById('sync-step-' + step);
  if (!el) return;
  var icons = {{'active':'<span style="display:inline-block;animation:spin 1s linear infinite">↻</span>','done':'✓','error':'✗'}};
  var colors = {{'active':'#6366f1','done':'#16a34a','error':'#ef4444'}};
  el.innerHTML = '<span style="color:' + colors[state] + ';margin-right:6px">' + icons[state] + '</span>' + el.dataset.label;
  el.style.color = state === 'active' ? '#374151' : (state === 'done' ? '#16a34a' : '#ef4444');
  el.style.fontWeight = state === 'active' ? '600' : '500';
}}


/* ── Modal search ─────────────────────────────────── */
function filterModal(q) {{
  q = (q || '').toLowerCase().trim();
  var anyVisible = false;
  document.querySelectorAll('.modal-site-row').forEach(function(row) {{
    var show = !q || (row.dataset.name || '').includes(q);
    row.style.display = show ? '' : 'none';
    if (show) anyVisible = true;
  }});
  document.querySelectorAll('.modal-cat-group').forEach(function(grp) {{
    var vis = Array.from(grp.querySelectorAll('.modal-site-row')).some(r => r.style.display !== 'none');
    grp.style.display = vis ? '' : 'none';
  }});
  document.getElementById('modal-no-results').style.display = (q && !anyVisible) ? '' : 'none';
}}

/* ── Existing account actions ─────────────────────── */
function toggleForm(key) {{
  var f = document.getElementById('form-' + key);
  f.style.display = f.style.display === 'none' ? 'block' : 'none';
}}

function toast(msg, ok) {{
  var t = document.getElementById('toast');
  t.textContent = msg;
  t.style.background = ok === false ? '#dc2626' : '#1a1a1a';
  t.classList.add('show');
  setTimeout(function() {{ t.classList.remove('show'); }}, 2500);
}}

/* ── First-sync progress overlay ─────────────────────────────────── */
function _showSyncOverlay(siteName) {{
  if (document.getElementById('sync-overlay')) return;
  var ol = document.createElement('div');
  ol.id = 'sync-overlay';
  ol.innerHTML = `
    <div id="sync-overlay-box">
      <div id="sync-ol-title">Connecting to <strong id="sync-ol-site"></strong></div>
      <div id="sync-ol-steps">
        <div class="sync-step sync-step-active" id="sync-step-connecting">
          <span class="sync-step-icon">⟳</span>
          <span class="sync-step-label">Logging in&hellip;</span>
        </div>
        <div class="sync-step sync-step-pending" id="sync-step-scraping">
          <span class="sync-step-icon">·</span>
          <span class="sync-step-label">Scanning your account&hellip;</span>
        </div>
        <div class="sync-step sync-step-pending" id="sync-step-discovering">
          <span class="sync-step-icon">·</span>
          <span class="sync-step-label">Finding your benefits&hellip;</span>
        </div>
      </div>
      <div id="sync-ol-result" style="display:none"></div>
      <a id="sync-ol-btn" href="/" style="display:none" class="btn-primary">View Dashboard →</a>
    </div>`;
  ol.querySelector('#sync-ol-site').textContent = siteName;
  document.body.appendChild(ol);
  /* inject styles once */
  if (!document.getElementById('sync-overlay-css')) {{
    var s = document.createElement('style');
    s.id = 'sync-overlay-css';
    s.textContent = `
      #sync-overlay {{
        position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;
        display:flex;align-items:center;justify-content:center;
      }}
      #sync-overlay-box {{
        background:#fff;border-radius:14px;padding:36px 40px;width:340px;
        box-shadow:0 8px 40px rgba(0,0,0,.18);font-family:inherit;
      }}
      #sync-ol-title {{
        font-size:16px;font-weight:600;color:#1a1a1a;margin-bottom:24px;
        text-align:center;
      }}
      .sync-step {{
        display:flex;align-items:center;gap:10px;padding:8px 0;
        border-bottom:1px solid #f0f0f0;font-size:14px;color:#333;
      }}
      .sync-step:last-of-type {{ border-bottom:none; }}
      .sync-step-pending {{ opacity:.35; }}
      .sync-step-done .sync-step-icon {{ color:#22a05a;font-style:normal; }}
      .sync-step-active .sync-step-icon {{ display:inline-block;animation:spin .8s linear infinite; }}
      .sync-step-error .sync-step-icon {{ color:#d04040; }}
      @keyframes spin {{ to {{ transform:rotate(360deg); }} }}
      #sync-ol-result {{
        margin-top:20px;text-align:center;font-size:15px;font-weight:600;color:#1a1a1a;
      }}
      #sync-ol-btn {{
        display:block;margin-top:16px;text-align:center;padding:10px 0;
        background:#6c47ff;color:#fff;border-radius:8px;font-weight:600;
        font-size:14px;text-decoration:none;
      }}
      #sync-ol-btn:hover {{ background:#5535e0; }}
    `;
    document.head.appendChild(s);
  }}
}}

function _updateSyncOverlay(step, fieldsFound, error) {{
  var stepOrder = ['connecting','scraping','discovering'];
  var labels = {{
    'connecting': 'Logging in…',
    'scraping':   'Scanning your account…',
    'discovering':'Finding your benefits…',
  }};
  stepOrder.forEach(function(s) {{
    var el = document.getElementById('sync-step-' + s);
    if (!el) return;
    var icon = el.querySelector('.sync-step-icon');
    var label = el.querySelector('.sync-step-label');
    el.className = 'sync-step';
    var stepIdx = stepOrder.indexOf(s);
    var curIdx  = stepOrder.indexOf(step);
    if (step === 'done' || stepIdx < curIdx) {{
      el.classList.add('sync-step-done');
      icon.textContent = '✓';
    }} else if (s === step) {{
      el.classList.add('sync-step-active');
      icon.textContent = '⟳';
      label.textContent = labels[s] || s;
    }} else {{
      el.classList.add('sync-step-pending');
      icon.textContent = '·';
    }}
  }});
  if (step === 'done') {{
    var resultEl = document.getElementById('sync-ol-result');
    var btn = document.getElementById('sync-ol-btn');
    var msg = fieldsFound > 0
      ? '🎉 Found ' + fieldsFound + ' benefit' + (fieldsFound !== 1 ? 's' : '')
      : 'Sync complete';
    resultEl.textContent = msg;
    resultEl.style.display = 'block';
    btn.style.display = 'block';
  }} else if (step === 'error') {{
    var resultEl = document.getElementById('sync-ol-result');
    resultEl.textContent = error || 'Sync failed — try again';
    resultEl.style.color = '#d04040';
    resultEl.style.display = 'block';
    var btn = document.getElementById('sync-ol-btn');
    btn.textContent = 'Back';
    btn.href = '#';
    btn.style.display = 'block';
    btn.onclick = function() {{ document.getElementById('sync-overlay').remove(); return false; }};
  }}
}}

function saveCred(key, siteName) {{
  siteName = siteName || key;
  var u = document.getElementById('u-' + key).value.trim();
  var p = document.getElementById('p-' + key).value;
  var t = document.getElementById('t-' + key) ? document.getElementById('t-' + key).value.trim() : '';
  if (!u || !p) {{ toast('Username and password required', false); return; }}
  var saveBtn = document.querySelector('#form-' + key + ' .btn-save');
  if (saveBtn) {{ saveBtn.textContent = 'Saving...'; saveBtn.disabled = true; }}
  fetch('/credentials/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF, source: key, username: u, password: p, totp_secret: t}})
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{
      /* Show the progress overlay */
      _showSyncOverlay(siteName);
      fetch('/sync/account/' + key, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: new URLSearchParams({{_csrf: CSRF}})
      }}).then(function() {{
        var poll = setInterval(function() {{
          fetch('/sync/status').then(r => r.json()).then(function(s) {{
            var step = s.step || (s.running ? 'connecting' : 'done');
            _updateSyncOverlay(step, s.fields_found || 0, s.error);
            if (!s.running) {{ clearInterval(poll); }}
          }});
        }}, 2000);
      }});
    }} else {{
      toast(d.error || 'Error', false);
      if (saveBtn) {{ saveBtn.textContent = 'Save & Sync'; saveBtn.disabled = false; }}
    }}
  }});
}}

function removeCred(key, name) {{
  if (!confirm('Remove credentials for ' + name + '?')) return;
  fetch('/credentials/delete/' + key, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF}})
  }}).then(() => location.reload());
}}

function resetFields(source) {{
  if (!confirm('Clear all discovered fields for this account? They will be re-discovered automatically on the next sync.')) return;
  fetch('/credentials/fields/reset/' + source, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF}})
  }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
    if (d.ok) {{ toast('Fields cleared — will re-discover on next sync'); setTimeout(function() {{ location.reload(); }}, 1200); }}
    else toast(d.error || 'Reset failed', false);
  }}).catch(function() {{ toast('Reset failed — try again', false); }});
}}

function saveFields(source, container) {{
  // When called from the modal, scope to modal body to avoid reading the hidden
  // page panel as well (duplicate IDs / double-count bug).
  var root = container || document;
  var boxes = root.querySelectorAll('[data-source="' + source + '"]');
  var enabled = Array.from(boxes).filter(b => b.checked).map(b => b.dataset.key);
  fetch('/credentials/fields', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF, source: source, enabled_fields: JSON.stringify(enabled)}})
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{
      toast('Saved ✓');
      closeFieldModal();
    }}
  }});
}}

function saveFieldsModal(source) {{
  var modalBody = document.getElementById('field-modal-body');
  saveFields(source, modalBody);
}}

/* ── Field edit modal ─────────────────────────────── */
function openFieldModal(source) {{
  _fieldModalSource = source;
  var panel = document.getElementById('fields-panel-' + source);
  var body = document.getElementById('field-modal-body');
  var title = document.getElementById('field-modal-title');
  // Find site name from card
  var card = document.getElementById('card-' + source);
  var nameEl = card ? card.querySelector('div[style*="font-weight:600"]') : null;
  title.textContent = (nameEl ? nameEl.textContent : source) + ' — Edit fields';
  if (panel) {{
    // Strip id attributes from the copy to avoid duplicate IDs in the DOM
    // (the original panel stays hidden with its ids intact).
    body.innerHTML = panel.innerHTML.replace(/ id="field-[^"]*"/g, '');
  }} else {{
    body.innerHTML = '<p style="font-size:13px;color:#9ca3af">No fields available yet. Sync this account first.</p>';
  }}
  var overlay = document.getElementById('field-modal-overlay');
  overlay.style.display = 'flex';
}}

function closeFieldModal() {{
  document.getElementById('field-modal-overlay').style.display = 'none';
  _fieldModalSource = '';
}}

function fieldOverlayClick(e) {{
  if (e.target === document.getElementById('field-modal-overlay')) closeFieldModal();
}}

function clearAndRediscover(source) {{
  if (!confirm('Clear all fields for this account and rediscover from the latest sync data?')) return;
  fetch('/credentials/fields/reset/' + source, {{
    method: 'POST',
    headers: {{'X-CSRF-Token': CSRF}}
  }}).then(r => r.json()).then(function(d) {{
    if (!d.ok) {{ alert('Reset failed'); return; }}
    // Trigger rediscovery from stored page text
    fetch('/credentials/discover/' + source, {{
      method: 'POST',
      headers: {{'X-CSRF-Token': CSRF}}
    }}).then(r => r.json()).then(function(d2) {{
      if (d2.ok) {{ toast('Fields rediscovered ✓'); setTimeout(function(){{ location.reload(); }}, 800); }}
      else {{ toast('Reset done — fields will appear after next sync'); setTimeout(function(){{ location.reload(); }}, 1200); }}
    }});
  }});
}}

// Auto-open field modal if navigated here via anchor (e.g. from dashboard "Modify fields")
(function() {{
  var hash = location.hash.slice(1);
  if (hash.startsWith('fields-')) {{
    var source = hash.replace('fields-', '');
    setTimeout(function() {{ openFieldModal(source); }}, 200);
  }}
}})();

// Auto-discover fields for connected accounts that need it
fetch('/credentials/auto-discover', {{method:'POST',
  headers:{{'Content-Type':'application/x-www-form-urlencoded'}},
  body:new URLSearchParams({{_csrf:CSRF}})}}).catch(function(){{}});

// Live-updating relative sync timestamps
function fmtRelative(ts) {{
  try {{
    var d = new Date(ts);
    var secs = Math.floor((Date.now() - d.getTime()) / 1000);
    if (secs < 60) return 'just now';
    var mins = Math.floor(secs / 60);
    if (mins < 60) return mins + ' minute' + (mins === 1 ? '' : 's') + ' ago';
    var hrs = Math.floor(mins / 60);
    if (hrs < 24) return hrs + ' hour' + (hrs === 1 ? '' : 's') + ' ago';
    var days = Math.floor(hrs / 24);
    return days + ' day' + (days === 1 ? '' : 's') + ' ago';
  }} catch(e) {{ return ''; }}
}}
function updateSyncTimes() {{
  document.querySelectorAll('[data-synced]').forEach(function(el) {{
    var ts = el.dataset.synced;
    if (!ts) return;
    var card = el.closest('[data-sync-status]');
    var syncStatus = card ? card.dataset.syncStatus : '';
    if (syncStatus === 'login_required') {{
      el.innerHTML = '<span style="font-size:11px;color:#dc2626;font-weight:700">🔐 Login required</span>';
      return;
    }}
    var rel = fmtRelative(ts);
    if (rel) {{
      var color = '#22c55e', icon = '✓', fw = '500';
      var secs2 = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
      var hrs2 = secs2 / 3600;
      if (hrs2 >= 72) {{ color = '#dc2626'; icon = '!'; fw = '700'; }}
      else if (hrs2 >= 48) {{ color = '#f59e0b'; icon = '~'; }}
      else if (hrs2 >= 24) {{ color = '#f59e0b'; icon = '~'; }}
      else if (hrs2 >= 2) {{ color = '#6b7280'; icon = '✓'; }}
      el.innerHTML = '<span style="font-size:11px;color:' + color + ';font-weight:' + fw + '">' + icon + ' Synced ' + rel + '</span>';
    }}
  }});
}}
updateSyncTimes();
setInterval(updateSyncTimes, 30000);

// Pre-check saved field preferences on load
fetch('/credentials/fields/load').then(r => r.json()).then(function(data) {{
  if (!data.ok) return;
  Object.entries(data.fields).forEach(function([source, enabled]) {{
    enabled.forEach(function(key) {{
      var box = document.getElementById('field-' + source + '-' + key);
      if (box) box.checked = true;
    }});
    if (enabled.length) {{
      var det = document.getElementById('fields-' + source);
      if (det) det.open = true;
    }}
  }});
}}).catch(function(){{}});
</script>
</body>
</html>"""


@app.route("/credentials")
@require_login
def credentials_page():
    user = get_db().execute(
        "SELECT * FROM users WHERE id=?", (session["user_id"],)
    ).fetchone()
    rows = get_db().execute(
        "SELECT source, username_enc, extra_enc FROM account_credentials WHERE user_id=?",
        (user["id"],)
    ).fetchall()
    configured = {r["source"] for r in rows}
    # Load extra data (discovered fields, totp, etc.) per source
    extra_by_source = {}
    for r in rows:
        if r["extra_enc"]:
            try:
                extra_by_source[r["source"]] = json.loads(
                    decrypt_cred(user["id"], r["extra_enc"])
                )
            except Exception:
                pass
    # Load sync timestamps from account_data
    sync_rows = get_db().execute(
        "SELECT source, synced_at FROM account_data WHERE user_id=?", (user["id"],)
    ).fetchall()
    synced_at_by_source = {r["source"]: r["synced_at"] for r in sync_rows if r["synced_at"]}
    return _build_credentials_page(user, configured, extra_by_source, synced_at_by_source)


@app.route("/api/extension/poll/<source>")
@require_login
def extension_poll(source):
    """Poll whether the extension has captured data for a given source.
    Used by the connect-account modal to detect when the extension has synced.
    """
    uid = session["user_id"]
    db  = get_db()
    # For custom_* sources the extension uses the generated key directly.
    # For known sources (delta, marriott, etc.) we look for the exact source key
    # OR any custom_* source captured recently that references this source.
    row = db.execute(
        "SELECT synced_at FROM account_data WHERE user_id=? AND source=? ORDER BY synced_at DESC LIMIT 1",
        (uid, source)
    ).fetchone()
    if row:
        return jsonify({"captured": True, "synced_at": row["synced_at"]})
    return jsonify({"captured": False})


@app.route("/api/benefits/archive", methods=["POST"])
@require_login
def benefits_archive():
    """Archive a benefit — hides it from Available Now. Reversible."""
    check_csrf()
    uid    = session["user_id"]
    source = request.form.get("source", "").strip()
    label  = request.form.get("label", "").strip()
    if not source or not label:
        return jsonify({"ok": False, "error": "source and label required"}), 400
    db = get_db()
    db.execute(
        "INSERT OR IGNORE INTO archived_benefits (user_id, source, label, archived_at) VALUES (?,?,?,?)",
        (uid, source, label, iso())
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/benefits/unarchive", methods=["POST"])
@require_login
def benefits_unarchive():
    """Restore an archived benefit back to Available Now."""
    check_csrf()
    uid    = session["user_id"]
    source = request.form.get("source", "").strip()
    label  = request.form.get("label", "").strip()
    if not source or not label:
        return jsonify({"ok": False, "error": "source and label required"}), 400
    db = get_db()
    db.execute(
        "DELETE FROM archived_benefits WHERE user_id=? AND source=? AND label=?",
        (uid, source, label)
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/credentials/<source>/username")
@require_login
def credentials_get_username(source):
    """Return the stored (decrypted) username for a source — used to prefill the Edit login modal."""
    uid = session["user_id"]
    row = get_db().execute(
        "SELECT username_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not row:
        return jsonify({"username": ""})
    try:
        username = decrypt_cred(uid, row["username_enc"])
    except Exception:
        username = ""
    return jsonify({"username": username})


@app.route("/credentials/register", methods=["POST"])
@require_login
def credentials_register():
    """Register an account source without credentials (extension-first flow).
    Creates the credential placeholder so the account shows up on the dashboard
    while the extension handles the actual data capture.
    """
    check_csrf()
    uid    = session["user_id"]
    source = request.form.get("source", "").strip()
    if not source:
        return jsonify({"ok": False, "error": "source required"}), 400
    db  = get_db()
    now = iso()
    existing = db.execute(
        "SELECT created_at FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not existing:
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (uid, source, "", "", "", now, now)
        )
        db.commit()
    # Create stub account_data row so supplement can fire on first visit
    site_meta = next(((n,i,c) for k,n,i,c,*_ in SUPPORTED_SITES if k == source), None)
    if not site_meta:
        site_meta = (source.replace('_',' ').title(), '🔗', '#f3f4f6')
    display, icon, color = site_meta
    stub_enc = encrypt_account_data(uid, {"sync_status": "needs_first_visit", "items": [], "raw_text": ""})
    db.execute(
        "INSERT OR IGNORE INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at) VALUES (?,?,?,?,?,?,?)",
        (uid, source, display, icon, color, stub_enc, iso())
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/credentials/save", methods=["POST"])
@require_login
def credentials_save():
    check_csrf()
    uid      = session["user_id"]
    source   = request.form.get("source", "").strip()
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    totp     = request.form.get("totp_secret", "").strip()

    if not source or not username:
        return jsonify({"ok": False, "error": "source and username required"}), 400

    extra = {}
    if totp: extra["totp_secret"] = totp

    now = iso()
    db  = get_db()
    existing = db.execute(
        "SELECT created_at, password_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    created = existing["created_at"] if existing else now
    # Preserve existing password if none supplied (blank = "keep existing")
    if password:
        new_password_enc = encrypt_cred(uid, password)
    elif existing and existing["password_enc"]:
        new_password_enc = existing["password_enc"]
    else:
        new_password_enc = ""

    db.execute(
        "INSERT OR REPLACE INTO account_credentials "
        "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (uid, source,
         encrypt_cred(uid, username),
         new_password_enc,
         encrypt_cred(uid, json.dumps(extra)) if extra else "",
         created, now)
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/credentials/fields", methods=["POST"])
@require_login
def credentials_fields_save():
    """Save which fields to show for an account."""
    check_csrf()
    uid    = session["user_id"]
    source = request.form.get("source", "").strip()
    try:
        enabled = json.loads(request.form.get("enabled_fields", "[]"))
    except Exception:
        return jsonify({"ok": False, "error": "invalid enabled_fields"}), 400

    # Merge into existing extra_enc
    db  = get_db()
    row = db.execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    extra = {}
    if row and row["extra_enc"]:
        try: extra = json.loads(decrypt_cred(uid, row["extra_enc"]))
        except Exception: pass
    extra["enabled_fields"] = enabled
    extra.pop("new_fields", None)  # clear new-field notification once user saves
    new_enc = encrypt_cred(uid, json.dumps(extra)) if extra else ""
    db.execute(
        "UPDATE account_credentials SET extra_enc=?, updated_at=? WHERE user_id=? AND source=?",
        (new_enc, iso(), uid, source)
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/credentials/auto-discover", methods=["POST"])
@require_login
def credentials_auto_discover():
    """Background: discover fields for any connected account missing them."""
    check_csrf()
    uid = session["user_id"]
    threading.Thread(target=_auto_discover_missing, args=(uid,), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/credentials/fields/reset/<source>", methods=["POST"])
@require_login
def credentials_fields_reset(source):
    """Clear all discovered fields for an account so re-discover starts fresh."""
    check_csrf()
    uid     = session["user_id"]
    db      = get_db()
    cred_row = db.execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    ex = {}
    if cred_row and cred_row["extra_enc"]:
        try: ex = json.loads(decrypt_cred(uid, cred_row["extra_enc"]))
        except Exception: pass
    ex.pop("enabled_fields", None)
    ex.pop("discovered_fields", None)
    ex.pop("last_raw_hash", None)   # force re-discovery on next sync
    ex.pop("new_fields", None)
    new_enc = encrypt_cred(uid, json.dumps(ex)) if ex else ""
    db.execute(
        "UPDATE account_credentials SET extra_enc=?, updated_at=? WHERE user_id=? AND source=?",
        (new_enc, iso(), uid, source)
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/credentials/discover/<source>", methods=["POST"])
@require_login
def credentials_discover(source):
    """Run AI field discovery for an account using the latest stored page text."""
    from werkzeug.exceptions import HTTPException
    try:
        check_csrf()
    except HTTPException:
        return jsonify({"ok": False, "error": "Session expired — refresh the page and try again"}), 403
    try:
        return _credentials_discover_impl(source)
    except Exception as e:
        print(f"[Mighty] Discover endpoint error: {e}", flush=True)
        return jsonify({"ok": False, "error": f"Server error: {str(e)[:100]}"}), 500

def _credentials_discover_impl(source):
    uid  = session["user_id"]

    # Get raw_text from last sync
    row  = get_db().execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "No sync data yet — sync this account first"}), 404

    data = decrypt_account_data(uid, row["data_enc"] or "")
    raw_text = data.get("raw_text", "")
    if not raw_text:
        return jsonify({"ok": False, "error": "No page text stored — sync again"}), 404

    if not _claude:
        return jsonify({"ok": False, "error": "Gemini API not configured — add GEMINI_API_KEY to Railway"}), 503

    # Find site display name
    site_name = next((name for key, name, *_ in SUPPORTED_SITES if key == source), source)

    # Single discovery call — the 60-second cache makes repeated calls return
    # identical results at temperature 0, so one call is sufficient.
    try:
        fields = claude_discover_fields(raw_text, site_name, source=source)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Discovery error: {str(e)[:100]}"}), 500
    if not fields:
        return jsonify({"ok": False, "error": "Could not identify fields — try syncing again"}), 500

    _save_discovered_fields(uid, source, fields)
    return jsonify({"ok": True, "fields": fields})




@app.route("/api/debug/raw/<source>", methods=["GET"])
@require_login
def api_debug_raw(source):
    """Show stored raw_text for a source — for debugging field discovery."""
    row = get_db().execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
        (session["user_id"], source)
    ).fetchone()
    if not row:
        return jsonify({"error": "No data stored for this source"}), 404
    data = decrypt_account_data(session["user_id"], row["data_enc"] or "")
    raw = data.get("raw_text", "")
    return jsonify({
        "source": source,
        "raw_text_len": len(raw),
        "raw_text_preview": raw[:2000],
        "items": data.get("items", []),
    })


@app.route("/api/data/force-discover/<source>", methods=["POST"])
@require_login
def api_force_discover(source):
    """Re-run Gemini field discovery on the existing stored raw_text for a source."""
    from werkzeug.exceptions import HTTPException
    try:
        check_csrf()
    except HTTPException:
        return jsonify({"ok": False, "error": "Session expired — refresh and try again"}), 403
    try:
        return _credentials_discover_impl(source)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Server error: {str(e)[:100]}"}), 500


@app.route("/api/data/rediscover-all", methods=["POST", "GET"])
@require_login
def api_rediscover_all():
    """Re-run field discovery on ALL accounts' existing raw_text in a background thread."""
    try:
        uid = session["user_id"]
        db  = get_db()
        rows = db.execute(
            "SELECT source, data_enc FROM account_data WHERE user_id=?", (uid,)
        ).fetchall()

        # Snapshot data needed by thread before request context closes
        rows_data = [(row["source"], row["data_enc"]) for row in rows]

        def _run_all():
            with app.app_context():
                for src, data_enc in rows_data:
                    try:
                        raw = decrypt_account_data(uid, data_enc or "").get("raw_text") or ""
                        if not raw:
                            continue
                        site_name = next((n for k, n, *_ in SUPPORTED_SITES if k == src),
                                         src.replace("_", " ").title())
                        fields = claude_discover_fields(raw, site_name, source=src)
                        if fields:
                            _save_discovered_fields(uid, src, fields)
                            print(f"[Rediscover] {src}: {len(fields)} fields", flush=True)
                        else:
                            print(f"[Rediscover] {src}: no fields found", flush=True)
                    except Exception as ex:
                        print(f"[Rediscover] {src}: error {ex}", flush=True)

        threading.Thread(target=_run_all, daemon=True).start()
        return jsonify({"ok": True, "sources": len(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)[:120]}), 500


@app.route("/api/fields-panel/<source>")
@require_login
def api_fields_panel(source):
    """Return the field-config panel HTML for a given source (used by dashboard modal)."""
    uid = session["user_id"]
    row = get_db().execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "html": ""})
    configured = {source}
    extra_data = {}
    if row["extra_enc"]:
        try:
            extra_data = json.loads(decrypt_cred(uid, row["extra_enc"]))
        except Exception:
            pass
    # Build inner HTML of the panel — strip the outer hidden div wrapper since we inline it
    raw = _field_config_html(source, configured, extra_data)
    # raw looks like: <div id="fields-panel-{src}" style="display:none">...content...</div>
    # Strip outer div so we can use content directly in the modal body
    inner = _re_mod.sub(r'^<div[^>]*>', '', raw.strip(), count=1)
    inner = _re_mod.sub(r'</div>$', '', inner.strip(), count=1)
    # Replace Save/Cancel/Clear buttons to use dashboard JS functions
    src_esc = source.replace("'", "\\'")
    inner = inner.replace(
        f"onclick=\"saveFieldsModal('{source}')\"",
        f"onclick=\"saveDashFields('{src_esc}')\""
    ).replace(
        "onclick=\"closeFieldModal()\"",
        "onclick=\"closeDashFieldModal()\""
    ).replace(
        f"onclick=\"clearAndRediscover('{source}')\"",
        f"onclick=\"clearAndRediscoverDash('{src_esc}')\""
    )
    return jsonify({"ok": True, "html": inner})


@app.route("/credentials/fields/load")
@require_login
def credentials_fields_load():
    """Return saved field preferences for all connected accounts."""
    uid  = session["user_id"]
    rows = get_db().execute(
        "SELECT source, extra_enc FROM account_credentials WHERE user_id=?", (uid,)
    ).fetchall()
    fields = {}
    for row in rows:
        if not row["extra_enc"]: continue
        try:
            extra = json.loads(decrypt_cred(uid, row["extra_enc"]))
            if "enabled_fields" in extra:
                fields[row["source"]] = extra["enabled_fields"]
        except Exception:
            pass
    return jsonify({"ok": True, "fields": fields})


@app.route("/credentials/delete/<source>", methods=["POST"])
@require_login
def credentials_delete(source):
    check_csrf()
    db = get_db()
    db.execute(
        "DELETE FROM account_credentials WHERE user_id=? AND source=?",
        (session["user_id"], source)
    )
    db.execute(
        "DELETE FROM account_data WHERE user_id=? AND source=?",
        (session["user_id"], source)
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/credentials", methods=["GET", "POST"])
def api_credentials_fetch():
    """Return decrypted credentials for the authenticated user (for the scraper).

    The scraper authenticates with its API key, gets back all credentials
    in plaintext — decryption happens server-side, so the scraper only
    needs the API key (no separate encryption password required).
    """
    user, _ = api_user()
    if not user:
        return jsonify({"ok": False, "error": "Invalid or missing api_key"}), 401

    rows = get_db().execute(
        "SELECT * FROM account_credentials WHERE user_id=?", (user["id"],)
    ).fetchall()

    creds   = {}
    email_cfg = None
    for row in rows:
        extra = {}
        if row["extra_enc"]:
            try:
                extra = json.loads(decrypt_cred(user["id"], row["extra_enc"]))
            except Exception:
                pass

        if row["source"] == "_email":
            email_cfg = {
                "address":      decrypt_cred(user["id"], row["username_enc"] or ""),
                "app_password": decrypt_cred(user["id"], row["password_enc"] or ""),
            }
        else:
            creds[row["source"]] = {
                "username":    decrypt_cred(user["id"], row["username_enc"] or ""),
                "password":    decrypt_cred(user["id"], row["password_enc"] or ""),
                "totp_secret": extra.get("totp_secret"),
            }

    return jsonify({"ok": True, "credentials": creds, "email": email_cfg})


# ── Login-page detection ──────────────────────────────────────────────────────

_LOGIN_SIGNALS = [
    "sign in to", "sign in with", "log in to", "log in with",
    "forgot password", "forgot your password", "reset password",
    "remember me", "create an account", "join now", "join for free",
    "email or member number", "member number or email",
    "username and password", "enter your password",
]

def _is_login_page(raw_text: str) -> bool:
    """Return True if raw_text looks like a login/sign-in page rather than account data."""
    if not raw_text:
        return False
    sample = raw_text[:3000].lower()
    hits = sum(1 for sig in _LOGIN_SIGNALS if sig in sample)
    return hits >= 2


# ── Account data sync API ─────────────────────────────────────────────────────

@app.route("/api/data/sync", methods=["POST"])
def api_data_sync():
    """Receive scraped account data from the local scraper and store it encrypted.

    Body (JSON):
        api_key   — user's Mighty API key
        source    — account key, e.g. "amex"
        data      — the result dict from the scraper (name, icon, color, status, items)
        synced_at — ISO timestamp of when the scrape ran (optional)
    """
    user, body = api_user()
    if not user:
        return jsonify({"ok": False, "error": "Invalid or missing api_key"}), 401

    source = (body.get("source") or "").strip().lower()
    if not source:
        return jsonify({"ok": False, "error": "source required"}), 400
    # Validate source: alphanumeric + underscore only, max 50 chars
    # Prevents log injection and storage of junk keys
    if len(source) > 50 or not re.match(r'^[a-z0-9_]+$', source):
        return jsonify({"ok": False, "error": "invalid source"}), 400

    data       = body.get("data") or {}
    synced_at  = body.get("synced_at") or iso()
    display    = data.get("name", source)
    icon       = data.get("icon", "?")
    color      = data.get("color", "#f0f0f0")
    raw_text   = (data.get("raw_text", "") or body.get("raw_text", ""))[:40_000]
    sync_source = (body.get("sync_source") or data.get("sync_source") or "railway").lower()
    is_gap_fill = bool(body.get("gap_fill", False))

    db = get_db()
    existing_row = db.execute(
        "SELECT data_enc, synced_at FROM account_data WHERE user_id=? AND source=?",
        (user["id"], source)
    ).fetchone()
    ex_data = decrypt_account_data(user["id"], existing_row["data_enc"] or "") if existing_row else {}

    # ── Gap-fill merge: append sub-page text to existing raw_text instead of replacing ──
    # Gap-fill payloads only contain certificate/wallet page text (targeted, small).
    # Replacing the full record would erase previously synced points, status, etc.
    if is_gap_fill and existing_row and ex_data.get("sync_status") == "ok":
        old_raw = ex_data.get("raw_text", "")
        if raw_text and raw_text.strip():
            # Prepend new text so it takes priority in AI extraction; cap total
            merged = (raw_text.strip() + "\n\n" + old_raw).strip()
            ex_data["raw_text"] = merged[:40_000]
        # Re-encrypt merged record and return — skip all the overwrite logic below
        data_enc = encrypt_account_data(user["id"], ex_data)
        db.execute(
            "UPDATE account_data SET data_enc=?, synced_at=? WHERE user_id=? AND source=?",
            (data_enc, synced_at, user["id"], source)
        )
        db.commit()
        _log_privacy_event(user["id"], "data_synced", source=source,
                           detail=f"gap_fill +{len(raw_text)} chars merged")
        # Trigger re-discovery on merged text in background (no Gemini call if content unchanged)
        _gf_uid    = user["id"]
        _gf_src    = source
        _gf_text   = ex_data.get("raw_text", "")
        _gf_name   = next((n for k, n, *_ in SUPPORTED_SITES if k == source), source.replace("_", " ").title())
        def _bg_gf_discover():
            with app.app_context():
                try:
                    fields = claude_discover_fields(_gf_text, _gf_name, source=_gf_src)
                    if fields:
                        _save_discovered_fields(_gf_uid, _gf_src, fields)
                        print(f"[GapFill] {_gf_src}: re-discovered {len(fields)} fields on merged text", flush=True)
                except Exception as _e:
                    print(f"[GapFill] {_gf_src}: re-discovery error: {_e}", flush=True)
        threading.Thread(target=_bg_gf_discover, daemon=True).start()
        return jsonify({"ok": True, "gap_fill": True, "merged_chars": len(ex_data.get("raw_text",""))})

    # Extension-first: if Railway is trying to sync but extension already has fresh good data, skip.
    # force=True (manual "Sync All") bypasses the recency check but still applies a quality gate:
    # never let a short/poor Railway scrape overwrite richer extension data.
    force = body.get("force", False)
    if sync_source == "railway" and ex_data.get("sync_source") == "extension" and existing_row:
        existing_raw_len = len(ex_data.get("raw_text", ""))
        new_raw_len      = len(raw_text)
        # Quality gate: new data must be at least 60% as long as existing to overwrite
        if existing_raw_len > 500 and new_raw_len < existing_raw_len * 0.6:
            print(f"[Mighty] Railway quality gate blocked {source} — new {new_raw_len} chars < 60% of existing {existing_raw_len}", flush=True)
            return jsonify({"ok": True, "skipped": True, "reason": "quality_gate"})
        if not force:
            try:
                import datetime as _dt
                age_h = (_dt.datetime.utcnow() - _dt.datetime.fromisoformat(
                    existing_row["synced_at"].rstrip("Z"))).total_seconds() / 3600
                if age_h < 2:
                    print(f"[Mighty] Railway skipping {source} — extension synced {age_h:.1f}h ago", flush=True)
                    return jsonify({"ok": True, "skipped": True, "reason": "extension_synced_recently"})
            except Exception:
                pass

    # Detect login-page redirects before storing
    if _is_login_page(raw_text):
        # Don't overwrite good existing data with a failed login-page scrape,
        # but DO mark the account as login_required so the card shows a warning.
        if ex_data.get("sync_status") == "ok":
            print(f"[Mighty] Login page detected for {source} — marking login_required (data preserved)", flush=True)
            ex_data["sync_status"]         = "login_required"
            ex_data["sync_failure_reason"] = "login_wall"
            db.execute(
                "UPDATE account_data SET data_enc=?, sync_status=?, sync_failure_reason=? WHERE user_id=? AND source=?",
                (encrypt_account_data(user["id"], ex_data), "login_required", "login_wall", user["id"], source)
            )
            db.commit()
            return jsonify({"ok": True, "updated": "login_required", "reason": "login_page_detected"})
        data["sync_status"] = "login_required"
        data["sync_failure_reason"] = "login_wall"
        data["items"] = []
        raw_text = ""
        data["raw_text"] = ""
    elif not data.get("items") and not raw_text:
        data["sync_status"] = "no_data"
        data["sync_failure_reason"] = "no_data"
    else:
        data["sync_status"] = "ok"
        data.pop("sync_failure_reason", None)

    data["sync_source"] = sync_source

    data_enc   = encrypt_account_data(user["id"], data)

    db = get_db()
    db.execute(
        """INSERT INTO account_data
           (user_id, source, display_name, icon, color, data_enc, synced_at, sync_failure_reason)
           VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(user_id, source) DO UPDATE SET
               display_name        = excluded.display_name,
               icon                = excluded.icon,
               color               = excluded.color,
               data_enc            = excluded.data_enc,
               synced_at           = excluded.synced_at,
               sync_failure_reason = excluded.sync_failure_reason""",
        (user["id"], source, display, icon, color, data_enc, synced_at,
         data.get("sync_failure_reason")),
    )
    db.commit()
    _log_privacy_event(user["id"], "data_synced", source=source, detail=f"{len(raw_text)} chars")

    # Persist action items from this sync so they survive re-renders
    try:
        populate_action_items(user["id"], source, data)
    except Exception as _pai_err:
        print(f"[Mighty] populate_action_items error: {_pai_err}", flush=True)

    # Auto-trigger field discovery if no field prefs exist yet for this source
    cred_row = db.execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
        (user["id"], source)
    ).fetchone()
    has_prefs = False
    if cred_row and cred_row["extra_enc"]:
        try:
            ex = json.loads(decrypt_cred(user["id"], cred_row["extra_enc"]))
            has_prefs = "enabled_fields" in ex
        except Exception:
            pass

    if raw_text and _claude and data.get("sync_status") == "ok":
        import threading
        site_name = display
        uid       = user["id"]
        # Materialise cred_row data before spawning threads — sqlite3.Row objects
        # reference the request-scoped connection which closes when the request ends.
        _cred_extra_enc = (cred_row["extra_enc"] if cred_row else None)

        if not has_prefs:
            # First-time: discover fields.
            # Everything runs inside app.app_context() so get_db() works throughout,
            # including the hint-phrase query inside claude_discover_fields.
            def _bg_discover():
                with app.app_context():
                    fields = claude_discover_fields(raw_text, site_name, source=source)
                    _db = get_db()
                    if fields:
                        # Route through _save_discovered_fields so hint population,
                        # confidence-based auto-selection, and failure tracking all apply.
                        _save_discovered_fields(uid, source, fields)
                        # Store snippets hash so the first _bg_refresh can skip
                        # discovery if the page content hasn't changed.
                        try:
                            import hashlib as _hl_d
                            _snip_d  = _extract_candidate_snippets(raw_text)
                            _hash_d  = _hl_d.md5(_snip_d.encode()).hexdigest()
                            _cred_d  = _db.execute(
                                "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
                                (uid, source)
                            ).fetchone()
                            _ex_d: dict = {}
                            if _cred_d and _cred_d["extra_enc"]:
                                try: _ex_d = json.loads(decrypt_cred(uid, _cred_d["extra_enc"]))
                                except Exception: pass
                            _ex_d["snippets_hash"] = _hash_d
                            _db.execute(
                                "UPDATE account_credentials SET extra_enc=?, updated_at=? "
                                "WHERE user_id=? AND source=?",
                                (encrypt_cred(uid, json.dumps(_ex_d)), iso(), uid, source)
                            )
                            _db.commit()
                        except Exception:
                            pass
                        # Path-specific quality score boost
                        try:
                            _paths = _paths_from_raw(raw_text)
                            if _paths:
                                for _p in _paths:
                                    _db.execute(
                                        "UPDATE site_paths SET quality_score = MIN(quality_score + 0.5, 10.0), "
                                        "last_seen = ? WHERE site = ? AND path = ?",
                                        (iso(), source, _p)
                                    )
                            else:
                                _db.execute(
                                    "UPDATE site_paths SET quality_score = MIN(quality_score + 0.2, 8.0), "
                                    "last_seen = ? WHERE site = ? AND quality_score < 5.0",
                                    (iso(), source)
                                )
                            _db.commit()
                        except Exception:
                            pass
                        # Negative learning: accelerated decay for nav/marketing paths
                        try:
                            for _p_cls in (_paths if _paths else []):
                                # Read path text from raw_text segment if present
                                import re as _re_cls
                                _seg_re = _re_cls.compile(
                                    r'(?:---|===)\s*https?://[^\s]*' + _re_cls.escape(_p_cls) + r'[^\n]*\n(.*?)(?=(?:---|===)\s*https?://|\Z)',
                                    _re_cls.dotall | _re_cls.ignorecase
                                )
                                _m_cls = _seg_re.search(raw_text)
                                _seg_text = _m_cls.group(1) if _m_cls else ""
                                _cls = _classify_path_content(_seg_text or raw_text[:2000])
                                if _cls in ("login_wall", "bot_challenge", "account_data"):
                                    pass  # no penalty
                                elif _cls == "empty":
                                    # tiny transient penalty only for already-middling paths
                                    try:
                                        _db.execute(
                                            "UPDATE site_paths SET failure_count = failure_count + 1 "
                                            "WHERE site=? AND path=? AND quality_score > 1.0",
                                            (source, _p_cls)
                                        )
                                    except Exception:
                                        pass
                                elif _cls in ("navigation", "marketing"):
                                    _db.execute(
                                        "UPDATE site_paths SET failure_count = failure_count + 2, "
                                        "quality_score = MAX(0.0, quality_score - 0.8) "
                                        "WHERE site=? AND path=?", (source, _p_cls)
                                    )
                            _db.commit()
                        except Exception:
                            pass
                        _record_path_failures(source, raw_text, succeeded=True)
                    else:
                        # Discovery ran but LLM returned nothing — set discovery_failed
                        # flag so the dashboard can show "couldn't read this account".
                        ex2 = {}
                        if _cred_extra_enc:
                            try: ex2 = json.loads(decrypt_cred(uid, _cred_extra_enc))
                            except Exception: pass
                        ex2["discovery_failed"] = True
                        ex2.setdefault("enabled_fields",    [])
                        ex2.setdefault("discovered_fields", [])
                        _db.execute(
                            "UPDATE account_credentials SET extra_enc=?, updated_at=? "
                            "WHERE user_id=? AND source=?",
                            (encrypt_cred(uid, json.dumps(ex2)), iso(), uid, source)
                        )
                        _db.commit()
                        _record_path_failures(source, raw_text, succeeded=False)
            threading.Thread(target=_bg_discover, daemon=True).start()
        else:
            # Existing prefs: re-run full discovery so new fields (credits, offers, certs)
            # from freshly-scraped benefit pages are picked up and merged in.
            def _bg_refresh():
                # Everything inside app context so get_db() works throughout,
                # including hint-phrase loading inside claude_discover_fields.
                with app.app_context():
                    import hashlib as _hl
                    _db_r = get_db()

                    # ── Snippet hash check — skip Gemini if content unchanged ────
                    # Extract snippets (with hints) and hash them. If the hash
                    # matches the last discovery run, the account data hasn't
                    # meaningfully changed — no need to spend a Gemini call.
                    _hint_phrases_r: list[str] = []
                    try:
                        _hint_phrases_r = [
                            r["trigger_phrase"] for r in _db_r.execute(
                                "SELECT trigger_phrase FROM extraction_hints WHERE site=? "
                                "ORDER BY success_count DESC, confidence DESC LIMIT 50",
                                (source,)
                            ).fetchall()
                        ]
                    except Exception:
                        pass

                    _snippets_r   = _extract_candidate_snippets(raw_text, hint_phrases=_hint_phrases_r)
                    _new_hash     = _hl.md5(_snippets_r.encode()).hexdigest()

                    _cred_r = _db_r.execute(
                        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
                        (uid, source)
                    ).fetchone()
                    _ex_r: dict = {}
                    if _cred_r and _cred_r["extra_enc"]:
                        try: _ex_r = json.loads(decrypt_cred(uid, _cred_r["extra_enc"]))
                        except Exception: pass

                    if _ex_r.get("snippets_hash") == _new_hash:
                        print(
                            f"[Mighty] Skipping re-discovery for {source} "
                            f"— snippets unchanged (hash {_new_hash[:8]})",
                            flush=True,
                        )
                        return

                    print(
                        f"[Mighty] Re-discovering {source} — snippets changed "
                        f"({(_ex_r.get('snippets_hash') or 'none')[:8]} → {_new_hash[:8]})",
                        flush=True,
                    )

                    # ── Run discovery ──────────────────────────────────────────
                    new_fields = claude_discover_fields(raw_text, site_name, source=source)
                    if not new_fields:
                        _record_path_failures(source, raw_text, succeeded=False)
                        return
                    _record_path_failures(source, raw_text, succeeded=True)
                    _save_discovered_fields(uid, source, new_fields)

                    # Persist the new hash so the next sync can skip if unchanged
                    _cred_r2 = _db_r.execute(
                        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
                        (uid, source)
                    ).fetchone()
                    _ex_r2: dict = {}
                    if _cred_r2 and _cred_r2["extra_enc"]:
                        try: _ex_r2 = json.loads(decrypt_cred(uid, _cred_r2["extra_enc"]))
                        except Exception: pass
                    _ex_r2["snippets_hash"] = _new_hash
                    _db_r.execute(
                        "UPDATE account_credentials SET extra_enc=?, updated_at=? "
                        "WHERE user_id=? AND source=?",
                        (encrypt_cred(uid, json.dumps(_ex_r2)), iso(), uid, source)
                    )
                    _db_r.commit()

                    # Self-improving coverage: boost specific paths that contributed
                    try:
                        _paths2 = _paths_from_raw(raw_text)
                        if _paths2:
                            for _p2 in _paths2:
                                _db_r.execute(
                                    "UPDATE site_paths SET quality_score = MIN(quality_score + 0.3, 10.0), "
                                    "last_seen = ? WHERE site = ? AND path = ?",
                                    (iso(), source, _p2)
                                )
                        else:
                            _db_r.execute(
                                "UPDATE site_paths SET quality_score = MIN(quality_score + 0.1, 8.0), "
                                "last_seen = ? WHERE site = ? AND quality_score < 5.0",
                                (iso(), source)
                            )
                        # Negative learning: accelerated decay for nav/marketing paths
                        try:
                            import re as _re_r
                            for _p_r in (_paths2 if _paths2 else []):
                                _seg_re_r = _re_r.compile(
                                    r'(?:---|===)\s*https?://[^\s]*' + _re_r.escape(_p_r) + r'[^\n]*\n(.*?)(?=(?:---|===)\s*https?://|\Z)',
                                    _re_r.dotall | _re_r.ignorecase
                                )
                                _m_r = _seg_re_r.search(raw_text)
                                _seg_r = _m_r.group(1) if _m_r else ""
                                _cls_r = _classify_path_content(_seg_r or raw_text[:2000])
                                if _cls_r in ("login_wall", "bot_challenge", "account_data"):
                                    pass  # no penalty
                                elif _cls_r == "empty":
                                    # tiny transient penalty only for already-middling paths
                                    try:
                                        _db_r.execute(
                                            "UPDATE site_paths SET failure_count = failure_count + 1 "
                                            "WHERE site=? AND path=? AND quality_score > 1.0",
                                            (source, _p_r)
                                        )
                                    except Exception:
                                        pass
                                elif _cls_r in ("navigation", "marketing"):
                                    _db_r.execute(
                                        "UPDATE site_paths SET failure_count = failure_count + 2, "
                                        "quality_score = MAX(0.0, quality_score - 0.8) "
                                        "WHERE site=? AND path=?", (source, _p_r)
                                    )
                            _db_r.commit()
                        except Exception:
                            pass
                        _db_r.commit()
                    except Exception:
                        pass
            threading.Thread(target=_bg_refresh, daemon=True).start()

    return jsonify({"ok": True, "source": source})


@app.route("/api/extension/intercept", methods=["POST"])
def api_extension_intercept():
    """Receive a JSON API response intercepted from the user's real browser session.
    Prepends the structured data to the account's raw_text and re-runs field discovery.
    Auth: X-Mighty-Key header.
    """
    user, _ = api_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401

    body      = request.get_json(silent=True) or {}
    source    = (body.get("source") or "").strip()
    url       = (body.get("url") or "").strip()
    json_data = body.get("json_data") or ""
    synced_at = body.get("synced_at") or iso()

    if not source or not json_data:
        return jsonify({"error": "source and json_data required"}), 400
    if len(source) > 50 or not re.match(r'^[a-z0-9_]+$', source):
        return jsonify({"error": "invalid source"}), 400

    uid = user["id"]
    db  = get_db()

    existing = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not existing:
        return jsonify({"ok": False, "error": "Account not connected"}), 404

    # Determine acquisition tier from URL prefix
    # embedded:KEY@https://... → Tier 2 (embedded page state: Next.js, Apollo, Redux)
    # normal URL              → Tier 1 (fetch/XHR API response)
    is_embedded = url.startswith("embedded:")
    acquisition_tier = 2 if is_embedded else 1
    tier_label = "EMBEDDED STATE" if is_embedded else "API RESPONSE"
    # For embedded URLs, extract the real page URL for domain checking + logging
    real_url = url.split("@", 1)[1] if is_embedded and "@" in url else url

    # Domain enforcement — truncate data from unexpected hostnames
    if not _url_allowed_for_source(source, real_url):
        json_data = json_data[:500]
        _log_privacy_event(uid, "domain_rejected", source=source, domain=real_url[:80])
        print(f"[Intercept] Domain mismatch for {source}: {real_url[:80]} — truncated", flush=True)

    ad      = decrypt_account_data(uid, existing["data_enc"] or "")
    old_raw = ad.get("raw_text", "")

    # Prepend the intercepted data so it leads the raw_text window.
    # Tier 2 (embedded) gets a distinct label so Gemini knows it's pre-hydration state.
    intercept_block = f"\n\n=== {tier_label}: {url} ===\n{json_data}\n"
    combined = (intercept_block + old_raw)[:40_000]
    ad["raw_text"] = combined

    db.execute(
        "UPDATE account_data SET data_enc=?, synced_at=? WHERE user_id=? AND source=?",
        (encrypt_account_data(uid, ad), synced_at, uid, source)
    )
    db.commit()
    _log_privacy_event(uid, "api_intercepted", source=source, domain=real_url[:80])
    print(f"[Intercept] {source} (tier {acquisition_tier}): {len(json_data)} chars from {url[:80]}", flush=True)

    # ── Connector fast path (deterministic, no Gemini) ───────────────────────
    # Try known JSON path extractors before falling back to Gemini.
    # connector_fields is passed into _bg() so it can merge with Gemini output.
    connector_fields = try_connector_paths(source, json_data)
    if connector_fields:
        print(
            f"[Intercept] {source} (tier {acquisition_tier}): "
            f"{len(connector_fields)} fields from connector (skipping Gemini for covered keys)",
            flush=True,
        )

    # Re-run field discovery in background
    if _claude:
        site_name = next((n for k, n, *_ in SUPPORTED_SITES if k == source),
                         source.replace("_", " ").title())
        # Capture current stored items for stale-data clearing below
        _current_items = list(ad.get("items") or ad.get("ai_items") or [])
        _connector_fields_snap = list(connector_fields)  # close over current value
        def _bg():
            with app.app_context():
                _tier_boost = 1.10 if acquisition_tier == 2 else 1.05
                _connector_keys = {f["key"] for f in _connector_fields_snap}

                # Always run Gemini to catch fields the connector doesn't cover
                fields = claude_discover_fields(combined, site_name, source=source)

                # Log candidate connector paths from Gemini's output
                if fields and json_data:
                    _record_connector_candidates(source, json_data, fields)

                # Merge: connector fields take priority for keys they cover,
                # Gemini fills in anything the connector missed.
                gemini_unique = [f for f in fields if f.get("key") not in _connector_keys]
                merged = _connector_fields_snap + gemini_unique

                if merged:
                    # Tag all fields with acquisition tier
                    for _f in merged:
                        _f["from_api"] = True
                        _f["acquisition_tier"] = acquisition_tier
                        # Connector fields already have confidence=0.99; only boost Gemini fields
                        if "confidence" in _f and not _f.get("from_connector"):
                            _f["confidence"] = min(0.99, _f["confidence"] * _tier_boost)
                    _save_discovered_fields(uid, source, merged)
                    connector_count = len(_connector_fields_snap)
                    gemini_count    = len(gemini_unique)
                    print(
                        f"[Intercept] {source} (tier {acquisition_tier}): "
                        f"{len(merged)} fields saved "
                        f"({connector_count} connector + {gemini_count} Gemini)",
                        flush=True,
                    )
                else:
                    # Discovery returned nothing usable. If all stored items are placeholder "–"
                    # values (stale defaults), clear them so they don't persist indefinitely.
                    _EMPTY_VALS = frozenset({"", "0", "-", "–", "—", "n/a", "none"})
                    _all_stale = _current_items and all(
                        str(it.get("value", "")).strip() in _EMPTY_VALS
                        for it in _current_items
                    )
                    if _all_stale:
                        try:
                            _db2 = get_db()
                            _ad2 = _db2.execute(
                                "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
                                (uid, source)
                            ).fetchone()
                            if _ad2:
                                _d2 = decrypt_account_data(uid, _ad2["data_enc"] or "")
                                _d2["items"] = []
                                _d2.pop("ai_items", None)
                                _db2.execute(
                                    "UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
                                    (encrypt_account_data(uid, _d2), uid, source)
                                )
                                _db2.commit()
                            print(f"[Intercept] {source}: cleared stale placeholder items", flush=True)
                        except Exception:
                            pass
        threading.Thread(target=_bg, daemon=True).start()

    _registry_report_path(source, url)
    return jsonify({"ok": True, "source": source, "chars": len(json_data)})


@app.route("/api/extension/supplement", methods=["POST"])
def api_extension_supplement():
    """Append page text from user's real browser to an existing account's raw_text and re-run discovery."""
    user, _ = api_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    source    = (body.get("source") or "").strip()
    url       = (body.get("url") or "").strip()
    new_text  = body.get("raw_text") or ""
    synced_at = body.get("synced_at") or iso()

    if not source or not new_text:
        return jsonify({"error": "source and raw_text required"}), 400
    if len(source) > 50 or not re.match(r'^[a-z0-9_]+$', source):
        return jsonify({"error": "invalid source"}), 400

    uid = user["id"]
    db  = get_db()

    existing = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not existing:
        site_meta = next(((n,i,c) for k,n,i,c,*_ in SUPPORTED_SITES if k == source), None)
        if not site_meta:
            site_meta = (source.replace('_',' ').title(), '🔗', '#f3f4f6')
        display, icon, color = site_meta
        stub_enc = encrypt_account_data(uid, {"sync_status": "needs_first_visit", "items": [], "raw_text": ""})
        db.execute(
            "INSERT OR IGNORE INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at) VALUES (?,?,?,?,?,?,?)",
            (uid, source, display, icon, color, stub_enc, iso())
        )
        db.commit()
        existing = db.execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
            (uid, source)
        ).fetchone()
        if not existing:
            return jsonify({"ok": False, "error": "Could not create account row"}), 500

    # Domain enforcement — truncate data from unexpected hostnames
    if not _url_allowed_for_source(source, url):
        new_text = new_text[:500]
        _log_privacy_event(uid, "domain_rejected", source=source, domain=url[:80])
        print(f"[Supplement] Domain mismatch for {source}: {url[:80]} — truncated", flush=True)

    ad = decrypt_account_data(uid, existing["data_enc"] or "")
    old_raw = ad.get("raw_text", "")

    # Cap individual submission to prevent a single call from flooding the window
    new_text = new_text[:20_000]
    # Prepend new page text so benefit data is prioritised in the 40k char window
    combined = f"\n\n--- {url} ---\n{new_text}\n\n" + old_raw
    combined = combined[:40_000]
    ad["raw_text"] = combined

    db.execute(
        "UPDATE account_data SET data_enc=?, synced_at=? WHERE user_id=? AND source=?",
        (encrypt_account_data(uid, ad), synced_at, uid, source)
    )
    db.commit()
    # Store entry_url on first successful capture so crawlAccount can use it
    if url:
        db.execute(
            "UPDATE account_data SET entry_url=? WHERE user_id=? AND source=? AND (entry_url IS NULL OR entry_url='')",
            (url, uid, source)
        )
        db.commit()
    print(f"[Supplement] {source}: appended {len(new_text)} chars from {url}", flush=True)

    if _claude:
        site_name = next((n for k, n, *_ in SUPPORTED_SITES if k == source),
                         source.replace("_", " ").title())
        def _bg():
            with app.app_context():
                fields = claude_discover_fields(combined, site_name, source=source)
                if fields:
                    _save_discovered_fields(uid, source, fields)
                    print(f"[Supplement] {source}: {len(fields)} fields", flush=True)
                    try:
                        _ad3 = get_db().execute(
                            "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
                            (uid, source)
                        ).fetchone()
                        if _ad3:
                            _d3 = decrypt_account_data(uid, _ad3["data_enc"] or "")
                            populate_action_items(uid, source, _d3)
                    except Exception as _pai_sup_err:
                        print(f"[Supplement] populate_action_items error: {_pai_sup_err}", flush=True)
        threading.Thread(target=_bg, daemon=True).start()

    _registry_report_path(source, url)
    return jsonify({"ok": True, "source": source, "chars_added": len(new_text)})


@app.route("/api/registry/report", methods=["POST"])
def registry_report():
    """Accept a {site, path} report and upsert into site_paths. No auth — paths aren't personal data."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if not _rate_limit(ip, "registry_report", limit=30, window=60):
        return jsonify({"ok": False, "error": "rate limited"}), 429
    body  = request.get_json(silent=True) or {}
    site  = (body.get("site") or "").strip().lower()
    path  = normalize_path((body.get("path") or "").strip())
    if not site or not path or path == '/':
        return jsonify({"ok": False}), 400
    db = get_db()
    db.execute('''
        INSERT INTO site_paths (site, path, reporter_count, last_seen, quality_score)
        VALUES (?, ?, 1, datetime('now'), 1.0)
        ON CONFLICT(site, path) DO UPDATE SET
            reporter_count = reporter_count + 1,
            last_seen      = datetime('now'),
            quality_score  = MIN(10.0, quality_score + 0.5)
    ''', (site, path))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/registry/paths", methods=["GET"])
def registry_paths():
    """Return trusted paths for a site, sorted by quality. Decays paths not seen in 30+ days."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if not _rate_limit(ip, "registry_paths", limit=60, window=60):
        return jsonify({"paths": []}), 429
    site = request.args.get("site", "").strip().lower()
    if not site:
        return jsonify({"paths": []})
    db = get_db()
    # Decay stale entries (not seen in 30+ days → lose 1 quality point, once per day max)
    db.execute('''
        UPDATE site_paths
        SET quality_score = MAX(0.0, quality_score - 1.0)
        WHERE site = ?
          AND julianday('now') - julianday(last_seen) > 30
          AND quality_score > 0
          AND julianday('now') - julianday(last_seen) < 31
    ''', (site,))
    db.commit()
    rows = db.execute('''
        SELECT path FROM site_paths
        WHERE site = ? AND quality_score > 0
        ORDER BY quality_score DESC, reporter_count DESC
        LIMIT 20
    ''', (site,)).fetchall()
    return jsonify({"paths": [r["path"] for r in rows]})


@app.route("/api/registry/remove", methods=["POST"])
def registry_remove():
    """Zero-out a known-bad path so it stops being served. No auth — paths aren't personal data."""
    ip = request.headers.get("X-Forwarded-For", request.remote_addr or "").split(",")[0].strip()
    if not _rate_limit(ip, "registry_remove", limit=10, window=60):
        return jsonify({"ok": False, "error": "rate limited"}), 429
    body = request.get_json(silent=True) or {}
    site = (body.get("site") or "").strip().lower()
    path = (body.get("path") or "").strip()
    if not site or not path:
        return jsonify({"ok": False}), 400
    db = get_db()
    db.execute(
        "UPDATE site_paths SET quality_score = 0.0 WHERE site = ? AND path = ?",
        (site, path)
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/debug/scrub-fields", methods=["POST"])
@require_login
def api_debug_scrub_fields():
    """Apply _post_filter_fields to all stored discovered fields directly in the DB,
    without needing to call Gemini. Useful when the filter was added after initial discovery."""
    uid = session["user_id"]
    db = get_db()
    rows = db.execute(
        "SELECT source, extra_enc FROM account_credentials WHERE user_id=?", (uid,)
    ).fetchall()
    updated = []
    for row in rows:
        if not row["extra_enc"]:
            continue
        try:
            ex = json.loads(decrypt_cred(uid, row["extra_enc"]))
        except Exception:
            continue
        fields = ex.get("discovered_fields", [])
        if not fields:
            continue
        filtered = _post_filter_fields(fields)
        removed_keys = {f["key"] for f in fields} - {f["key"] for f in filtered}
        if not removed_keys:
            continue
        ex["discovered_fields"] = filtered
        ex["enabled_fields"] = [k for k in ex.get("enabled_fields", []) if k not in removed_keys]
        db.execute(
            "UPDATE account_credentials SET extra_enc=?, updated_at=? WHERE user_id=? AND source=?",
            (encrypt_cred(uid, json.dumps(ex)), iso(), uid, row["source"])
        )
        updated.append({"source": row["source"], "removed": list(removed_keys)})
    db.commit()
    return jsonify({"ok": True, "updated": updated})


@app.route("/api/debug/fields/<source>")
@require_login_or_key
def api_debug_fields(source):
    """Debug: return every extracted AI item for an account from account_data,
    with its classified benefit type. Reads the synced data table, not credentials."""
    try:
        uid = get_current_user_id()
        row = get_db().execute(
            "SELECT data_enc, synced_at FROM account_data WHERE user_id=? AND source=?",
            (uid, source)
        ).fetchone()
        if not row:
            return jsonify({"error": "no synced data found for source", "source": source})
        try:
            data = decrypt_account_data(uid, row["data_enc"] or "")
        except Exception as e:
            return jsonify({"error": "decrypt failed: " + str(e)})
        items = data.get("ai_items") or data.get("items") or []
        out = []
        for it in items:
            lbl = it.get("label", "")
            val = str(it.get("value", ""))
            btype = it.get("_type") or classify_benefit(lbl, val, source)
            out.append({"label": lbl, "value": val, "type": btype, "key": it.get("key", "")})
        out.sort(key=lambda x: x["type"])
        raw = data.get("raw_text", "")
        # Find sections that look like account data (contain numbers, status words)
        import re as _dbg_re
        _status_words = ["gold","silver","platinum","diamond","points","miles","balance",
                         "certificate","credit","upgrade","companion","status","tier","member"]
        _relevant_chunks = []
        for _chunk in raw.split("\n\n"):
            _cl = _chunk.lower()
            if any(w in _cl for w in _status_words) and len(_chunk.strip()) > 20:
                _relevant_chunks.append(_chunk.strip()[:300])
        return jsonify({
            "source": source,
            "synced_at": row["synced_at"],
            "sync_source": data.get("sync_source", "unknown"),
            "item_count": len(out),
            "items": out,
            "raw_text_chars": len(raw),
            "raw_text_start": raw[:500],
            "relevant_chunks": _relevant_chunks[:10],
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/admin/site-health")
@require_login
def api_admin_site_health():
    """Return per-site extraction health across all users.

    For each connected source, reports:
      - connected_accounts  — number of users with this source connected
      - accounts_with_data  — accounts that have ≥1 extracted field
      - extraction_rate     — percentage with data
      - connector_supported — whether SITE_CONNECTORS has paths for this source
      - last_synced         — most recent sync timestamp across all users
      - field_keys          — union of all field keys seen across users (sample)
    """
    uid = session.get("user_id")
    if not uid:
        return jsonify({"error": "unauthorized"}), 401

    db = get_db()

    # Gather all account_data rows (all users in multi-user mode, just this user otherwise)
    # For privacy, we only expose aggregate counts, never individual values.
    rows = db.execute(
        "SELECT user_id, source, data_enc, synced_at FROM account_data ORDER BY source"
    ).fetchall()

    # Group by source
    from collections import defaultdict as _dd
    site_stats: dict = _dd(lambda: {
        "connected_accounts": 0,
        "accounts_with_data": 0,
        "last_synced": None,
        "field_keys": set(),
        "connector_supported": False,
        "example_values": {},
    })

    for row in rows:
        src = row["source"]
        st  = site_stats[src]
        st["connected_accounts"] += 1
        st["connector_supported"] = src in SITE_CONNECTORS

        if row["synced_at"] and (not st["last_synced"] or row["synced_at"] > st["last_synced"]):
            st["last_synced"] = row["synced_at"]

        try:
            ad    = decrypt_account_data(row["user_id"], row["data_enc"] or "")
            items = ad.get("items") or ad.get("ai_items") or []

            # Also check account_credentials.discovered_fields — fields are saved
            # there by _save_discovered_fields and may not always be synced back
            # to account_data.items (e.g. after a Railway re-sync clears items).
            if not items:
                cred_row = db.execute(
                    "SELECT extra_enc FROM account_credentials "
                    "WHERE user_id=? AND source=?",
                    (row["user_id"], row["source"])
                ).fetchone()
                if cred_row and cred_row["extra_enc"]:
                    try:
                        ex = json.loads(decrypt_cred(row["user_id"], cred_row["extra_enc"]))
                        items = ex.get("discovered_fields") or []
                    except Exception:
                        pass

            if items:
                st["accounts_with_data"] += 1
                for it in items:
                    k = it.get("key", "")
                    v = str(it.get("value", "")).strip()
                    if k:
                        st["field_keys"].add(k)
                        # Capture one example value per field key (from this user)
                        if k not in st["example_values"] and v and v not in ("-", "–", ""):
                            st["example_values"][k] = v
        except Exception:
            pass

    # Serialize sets; compute derived metrics
    result = []
    for src, st in sorted(site_stats.items()):
        connected = st["connected_accounts"]
        with_data  = st["accounts_with_data"]
        rate = round(with_data / connected * 100) if connected else 0
        result.append({
            "source":              src,
            "connected_accounts":  connected,
            "accounts_with_data":  with_data,
            "extraction_rate_pct": rate,
            "connector_supported": st["connector_supported"],
            "last_synced":         st["last_synced"],
            "field_keys":          sorted(st["field_keys"]),
            "example_values":      st["example_values"],
            "status": (
                "green"  if rate >= 80 else
                "yellow" if rate >= 30 else
                "red"
            ),
        })

    return jsonify({"sites": result, "total_sites": len(result)})


@app.route("/api/debug/discover-now/<source>")
@require_login
def api_debug_discover_now(source):
    """Run field discovery synchronously and expose every intermediate step.
    GET /api/debug/discover-now/hilton
    Returns: snippets sent to Gemini, raw Gemini text, pre-filter fields, post-filter fields."""
    uid = session["user_id"]
    try:
        row = get_db().execute(
            "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
            (uid, source)
        ).fetchone()
        if not row:
            return jsonify({"error": "no data found for source"}), 404
        data = decrypt_account_data(uid, row["data_enc"] or "")
        raw = data.get("raw_text", "")
        if not raw:
            return jsonify({"error": "raw_text is empty"}), 400

        site_name = next((n for k, n, *_ in SUPPORTED_SITES if k == source),
                         source.replace("_", " ").title())

        # Build snippets (identical to what claude_discover_fields uses)
        _hint_rows = get_db().execute(
            "SELECT trigger_phrase FROM extraction_hints WHERE site=? "
            "ORDER BY success_count DESC LIMIT 50", (source,)
        ).fetchall()
        _hint_phrases = [r["trigger_phrase"] for r in _hint_rows]
        snippets = _extract_candidate_snippets(raw, hint_phrases=_hint_phrases)

        # Build prompt (identical to claude_discover_fields)
        schema = _get_category_schema(source or "")
        from datetime import datetime as _dtm
        _today_str = _dtm.utcnow().strftime("%B %d, %Y")
        if schema:
            category_hint = (
                f"\nThis is a {schema['name']}. "
                f"Prioritise these field types:\n  {schema['priority_fields']}\n"
            )
        else:
            category_hint = ""
        prompt = DISCOVER_PROMPT.format(
            site=site_name, text=snippets,
            today=_today_str, category_hint=category_hint
        )

        # Call Gemini directly so we can capture the raw response
        gemini_raw_text = None
        gemini_error = None
        fields_before_filter = []
        fields_after_filter = []

        if _claude:
            _models = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-preview-05-20"]
            for _m in _models:
                try:
                    resp = _claude.models.generate_content(
                        model=_m,
                        contents=prompt,
                        config=_genai_sdk.types.GenerateContentConfig(
                            response_mime_type="application/json",
                            temperature=0,
                        ),
                    )
                    gemini_raw_text = resp.text.strip() if resp.text else ""
                    gemini_error = f"used model: {_m}"
                    break
                except Exception as _me:
                    gemini_error = f"model {_m} failed: {_me}"

            if gemini_raw_text:
                try:
                    parsed = json.loads(gemini_raw_text)
                    if isinstance(parsed, list):
                        fields_before_filter = parsed
                    elif isinstance(parsed, dict):
                        for k in ("fields", "data", "items", "results"):
                            if isinstance(parsed.get(k), list):
                                fields_before_filter = parsed[k]; break
                except json.JSONDecodeError:
                    m = re.search(r'\[.*\]', gemini_raw_text, re.DOTALL)
                    if m:
                        try: fields_before_filter = json.loads(m.group())
                        except Exception: pass

                fields_after_filter = _post_filter_fields(fields_before_filter)
        else:
            gemini_error = "Gemini client not configured"

        return jsonify({
            "source": source,
            "raw_text_chars": len(raw),
            "snippets_chars": len(snippets),
            "snippets_preview": snippets[:3000],
            "gemini_status": gemini_error,
            "gemini_raw_preview": (gemini_raw_text or "")[:2000],
            "fields_before_filter": fields_before_filter,
            "fields_after_filter": fields_after_filter,
            "field_count_before": len(fields_before_filter),
            "field_count_after": len(fields_after_filter),
        })
    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "trace": traceback.format_exc()}), 500


@app.route("/api/data/reclassify-all", methods=["POST"])
@require_login
def api_reclassify_all():
    """Re-stamp _type on every stored ai_item using the current classifier.
    No Gemini calls — pure in-process classification. Safe to call anytime."""
    try:
        check_csrf()
    except Exception:
        return jsonify({"ok": False, "error": "Session expired"}), 403
    uid = session["user_id"]
    db  = get_db()
    rows = db.execute(
        "SELECT source, data_enc FROM account_data WHERE user_id=?", (uid,)
    ).fetchall()
    updated_sources = []
    for row in rows:
        src = row["source"]
        try:
            data = decrypt_account_data(uid, row["data_enc"] or "")
        except Exception:
            continue
        items = data.get("ai_items") or data.get("items") or []
        changed = 0
        for it in items:
            lbl  = it.get("label", "")
            val  = str(it.get("value", ""))
            new_type = classify_benefit(lbl, val, src)
            if it.get("_type") != new_type:
                it["_type"] = new_type
                changed += 1
        if changed:
            data["ai_items"] = items
            db.execute(
                "UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
                (encrypt_account_data(uid, data), uid, src)
            )
            updated_sources.append({"source": src, "reclassified": changed})
    db.commit()
    return jsonify({"ok": True, "updated": updated_sources, "total_sources": len(rows)})


@app.route("/api/debug/provenance/<source>")
@require_login
def api_debug_provenance(source):
    """Internal debug view: return discovered fields with confidence and source_snippet.
    Useful for evaluating extraction quality without touching the main UI.

    Example: GET /api/debug/provenance/delta
    Returns each field with key, label, value, confidence (0-1), and the verbatim
    source_snippet from the page text that Gemini cited as evidence.
    Fields are sorted by confidence descending so low-confidence extractions are easy to spot.
    """
    uid = session["user_id"]
    row = get_db().execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    try:
        ex = json.loads(decrypt_cred(uid, row["extra_enc"]))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    fields = ex.get("discovered_fields", [])
    enabled_set = set(ex.get("enabled_fields", []))

    provenance = []
    for f in fields:
        confidence = f.get("confidence")
        provenance.append({
            "key":           f.get("key"),
            "label":         f.get("label"),
            "value":         f.get("value"),
            "enabled":       f.get("key") in enabled_set,
            "confidence":    confidence,
            "source_snippet": f.get("source_snippet"),
            # Flag anything below 0.80 — likely noise or hallucination
            "low_confidence": isinstance(confidence, (int, float)) and confidence < 0.80,
        })

    # Sort by confidence descending (None / missing goes to the bottom)
    provenance.sort(key=lambda x: x["confidence"] if isinstance(x["confidence"], (int, float)) else -1, reverse=True)

    return jsonify({
        "source": source,
        "discovered_at": ex.get("discovered_at"),
        "total": len(provenance),
        "low_confidence_count": sum(1 for p in provenance if p["low_confidence"]),
        "fields": provenance,
    })


@app.route("/api/field-history/<source>")
@require_login
def api_field_history(source):
    """Return recent field value changes for an account (last 30 days)."""
    uid = session["user_id"]
    rows = get_db().execute(
        "SELECT field_label, old_value, new_value, changed_at FROM field_history "
        "WHERE user_id=? AND source=? ORDER BY changed_at DESC LIMIT 50",
        (uid, source)
    ).fetchall()
    return jsonify({"source": source, "changes": [dict(r) for r in rows]})


@app.route("/api/reminders")
@require_login
def api_reminders():
    """Return actionable reminders for all accounts."""
    uid = session["user_id"]
    reminders = _get_reminders(uid)
    change_alerts = _get_change_alerts(uid)
    all_reminders = reminders + change_alerts
    # sort: urgent first, then soon, then info
    priority = {"urgent": 0, "soon": 1, "info": 2}
    all_reminders.sort(key=lambda x: priority.get(x.get("urgency", "info"), 2))

    # Filter snoozed reminders
    import datetime as _dt
    now_iso = _dt.datetime.utcnow().isoformat()
    try:
        snoozed_rows = get_db().execute(
            "SELECT reminder_key FROM reminder_snoozes WHERE user_id=? AND snoozed_until > ?",
            (uid, now_iso)
        ).fetchall()
        snoozed_keys = {r["reminder_key"] for r in snoozed_rows}
        def _reminder_key(r):
            return "{}::{}".format(r.get("type",""), r.get("source",""))
        all_reminders = [r for r in all_reminders if _reminder_key(r) not in snoozed_keys]
    except Exception:
        pass

    return jsonify({"reminders": all_reminders})


@app.route("/api/reminders/summary")
@require_login
def api_reminders_summary():
    """
    Returns a cross-account summary: groups all reminders by type/theme,
    not by account. Shows what the user is collectively forgetting.
    """
    uid = session["user_id"]
    try:
        reminders = _get_reminders(uid)
        change_alerts = _get_change_alerts(uid)
        all_items = reminders + change_alerts

        # Filter snoozed
        import datetime as _dt
        now_iso = _dt.datetime.utcnow().isoformat()
        snoozed = {r["reminder_key"] for r in get_db().execute(
            "SELECT reminder_key FROM reminder_snoozes WHERE user_id=? AND snoozed_until > ?",
            (uid, now_iso)
        ).fetchall()}
        all_items = [r for r in all_items if f"{r.get('type','')}::{r.get('source','')}" not in snoozed]

        # Group by theme
        themes = {
            "expiring": {"label": "Expiring benefits", "icon": "\U0001f4c5", "items": []},
            "bill":     {"label": "Bill changes",      "icon": "\U0001f4cb", "items": []},
            "unused":   {"label": "Unused credits",    "icon": "\U0001f4a1", "items": []},
            "payment":  {"label": "Payments due",      "icon": "\U0001f4b3", "items": []},
        }
        for r in all_items:
            rtype = r.get("type", "")
            if rtype in ("expiry",):
                themes["expiring"]["items"].append(r)
            elif rtype in ("bill_increase", "value_drop"):
                themes["bill"]["items"].append(r)
            elif rtype in ("unused_credit", "credit_added"):
                themes["unused"]["items"].append(r)
            elif rtype in ("payment_due",):
                themes["payment"]["items"].append(r)

        summary = []
        for theme_key, theme in themes.items():
            if theme["items"]:
                urgent_count = sum(1 for i in theme["items"] if i.get("urgency") == "urgent")
                summary.append({
                    "theme": theme_key,
                    "label": theme["label"],
                    "icon": theme["icon"],
                    "count": len(theme["items"]),
                    "urgent_count": urgent_count,
                    "items": theme["items"][:3],  # top 3 per theme
                })

        return jsonify({
            "total": len(all_items),
            "urgent": sum(1 for i in all_items if i.get("urgency") == "urgent"),
            "themes": summary,
        })
    except Exception as e:
        return jsonify({"total": 0, "urgent": 0, "themes": [], "error": str(e)})


@app.route("/api/reminders/snooze", methods=["POST"])
@require_login
def api_reminders_snooze():
    check_csrf()
    uid = session["user_id"]
    body = request.get_json(silent=True) or {}
    rtype = body.get("type", "")
    source = body.get("source", "")
    days = min(int(body.get("days", 7)), 365)

    if not rtype:
        return jsonify({"error": "missing type"}), 400

    reminder_key = "{}::{}".format(rtype, source)
    import datetime as _dt
    snoozed_until = (_dt.datetime.utcnow() + _dt.timedelta(days=days)).isoformat()

    db = get_db()
    db.execute("""
        INSERT INTO reminder_snoozes (user_id, reminder_key, snoozed_until, created_at)
        VALUES (?,?,?,?)
        ON CONFLICT(user_id, reminder_key) DO UPDATE SET snoozed_until=excluded.snoozed_until
    """, (uid, reminder_key, snoozed_until, iso()))
    db.commit()
    return jsonify({"ok": True, "snoozed_until": snoozed_until})


@app.route("/api/action-items/<int:item_id>/dismiss", methods=["POST"])
@require_login
def api_action_item_dismiss(item_id):
    check_csrf()
    uid = session["user_id"]
    import datetime as _dt_dim
    now = _dt_dim.datetime.utcnow().isoformat()
    db = get_db()
    rowcount = db.execute(
        "UPDATE action_items SET dismissed_at=? WHERE id=? AND user_id=?",
        (now, item_id, uid)
    ).rowcount
    db.commit()
    if rowcount == 0:
        return jsonify({"error": "not found"}), 404
    try:
        _recompute_type_affinity(uid)
    except Exception:
        pass
    return jsonify({"ok": True})


@app.route("/api/action-items/<int:item_id>/snooze", methods=["POST"])
@require_login
def api_action_item_snooze(item_id):
    check_csrf()
    uid = session["user_id"]
    body = request.get_json(silent=True) or {}
    days = min(int(body.get("days", 7)), 365)
    import datetime as _dt_snz
    snoozed_until = (_dt_snz.datetime.utcnow() + _dt_snz.timedelta(days=days)).isoformat()
    db = get_db()
    rowcount = db.execute(
        "UPDATE action_items SET snoozed_until=? WHERE id=? AND user_id=?",
        (snoozed_until, item_id, uid)
    ).rowcount
    db.commit()
    if rowcount == 0:
        return jsonify({"error": "not found"}), 404
    try:
        _recompute_type_affinity(uid)
    except Exception:
        pass
    return jsonify({"ok": True, "snoozed_until": snoozed_until})


@app.route("/api/action-items/<int:item_id>/complete", methods=["POST"])
@require_login
def api_action_item_complete(item_id):
    """Mark an action item as completed.  Preserves the row as a training signal."""
    check_csrf()
    uid = session["user_id"]
    import datetime as _dt_cmp
    now = _dt_cmp.datetime.utcnow().isoformat()
    db = get_db()
    rowcount = db.execute(
        "UPDATE action_items SET completed_at=? WHERE id=? AND user_id=?",
        (now, item_id, uid)
    ).rowcount
    db.commit()
    if rowcount == 0:
        return jsonify({"error": "not found"}), 404
    try:
        _recompute_type_affinity(uid)
    except Exception:
        pass
    return jsonify({"ok": True, "completed_at": now})


@app.route("/candidates/<source>")
@require_login
def candidates_page(source):
    uid = session["user_id"]
    rows = get_db().execute(
        "SELECT * FROM field_candidates WHERE user_id=? AND source=? AND status='pending' ORDER BY confidence DESC",
        (uid, source)
    ).fetchall()

    items_html = ""
    for r in rows:
        snip_html = (
            f'<div style="font-size:11px;color:#9ca3af;font-style:italic;margin-top:4px">{he(r["source_snippet"][:150])}</div>'
            if r['source_snippet'] else ''
        )
        row_id = r['id']
        conf_pct = int(r['confidence'] * 100)
        conf_label = _confidence_label(r['confidence'])
        items_html += (
            f'<div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;padding:14px 16px;margin-bottom:10px">'
            f'<div style="display:flex;justify-content:space-between;align-items:flex-start">'
            f'<div>'
            f'<div style="font-size:13px;font-weight:600;color:#111">{he(r["field_label"])}</div>'
            f'<div style="font-size:15px;color:#374151;margin:3px 0">{he(r["field_value"])}</div>'
            f'{snip_html}'
            f'</div>'
            f'<div style="font-size:11px;color:#6b7280;text-align:right">'
            f'<div style="margin-bottom:6px">{conf_label} confidence</div>'
            f'<div style="display:flex;gap:6px">'
            f'<button onclick="act({row_id},\'approve\')" style="padding:4px 10px;background:#22c55e;color:#fff;border:none;border-radius:4px;font-size:12px;cursor:pointer">Add to card</button>'
            f'<button onclick="act({row_id},\'dismiss\')" style="padding:4px 10px;background:#f3f4f6;color:#6b7280;border:1px solid #e5e7eb;border-radius:4px;font-size:12px;cursor:pointer">Dismiss</button>'
            f'</div></div></div></div>'
        )

    if not items_html:
        items_html = '<p style="color:#9ca3af;font-size:13px;text-align:center;padding:20px">No pending candidates</p>'

    return render_template_string("""<!DOCTYPE html><html><head><title>Review Candidates — Mighty</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:-apple-system,sans-serif;margin:0;background:#f9fafb}
.container{max-width:600px;margin:0 auto;padding:24px}</style></head>
<body><div class="container">
<div style="margin-bottom:16px"><a href="/dashboard" style="color:#6b7280;font-size:13px;text-decoration:none">← Dashboard</a></div>
<h2 style="font-size:18px;font-weight:700;color:#111;margin:0 0 4px">Possible new benefits</h2>
<p style="font-size:13px;color:#6b7280;margin:0 0 16px">Mighty found these but isn't sure enough to show them automatically.</p>
""" + items_html + """
</div>
<script>
const CSRF = '""" + get_csrf_token() + """';
async function act(id, action) {
  await fetch('/api/candidates/'+id+'/'+action, {
    method:'POST', headers:{'X-CSRF-Token': CSRF}
  });
  location.reload();
}
</script>
</body></html>""")


@app.route("/api/candidates/count")
@require_login
def api_candidates_count():
    uid = session["user_id"]
    counts = get_db().execute(
        "SELECT source, COUNT(*) as cnt FROM field_candidates "
        "WHERE user_id=? AND status='pending' GROUP BY source",
        (uid,)
    ).fetchall()
    total = sum(r["cnt"] for r in counts)
    by_source = {r["source"]: r["cnt"] for r in counts}
    return jsonify({"total": total, "by_source": by_source})


@app.route("/api/candidates/<int:cid>/approve", methods=["POST"])
@require_login
def api_candidate_approve(cid):
    check_csrf()
    uid = session["user_id"]
    db = get_db()
    row = db.execute(
        "SELECT * FROM field_candidates WHERE id=? AND user_id=?", (cid, uid)
    ).fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    ad_row = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?", (uid, row["source"])
    ).fetchone()
    if ad_row:
        d = decrypt_account_data(uid, ad_row["data_enc"] or "")
        items = d.get("items") or d.get("ai_items") or []
        new_item = {
            "key": row["field_key"],
            "label": row["field_label"],
            "value": row["field_value"],
            "confidence": row["confidence"],
            "source_snippet": row["source_snippet"],
            "_type": classify_benefit(row["field_label"], str(row["field_value"]), row["source"]),
        }
        existing_keys = {item.get("key") for item in items}
        if row["field_key"] in existing_keys:
            items = [new_item if item.get("key") == row["field_key"] else item for item in items]
        else:
            items.append(new_item)
        d["items"] = items
        db.execute("UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
                   (encrypt_account_data(uid, d), uid, row["source"]))
    db.execute("UPDATE field_candidates SET status='approved' WHERE id=?", (cid,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/candidates/<int:cid>/dismiss", methods=["POST"])
@require_login
def api_candidate_dismiss(cid):
    check_csrf()
    uid = session["user_id"]
    get_db().execute(
        "UPDATE field_candidates SET status='dismissed' WHERE id=? AND user_id=?", (cid, uid)
    )
    get_db().commit()
    return jsonify({"ok": True})


@app.route("/api/sync-health/<source>")
@require_login
def api_sync_health(source):
    """Return sync health metadata for one account."""
    uid = session["user_id"]
    db  = get_db()

    ad_row = db.execute(
        "SELECT data_enc, synced_at, sync_failure_reason FROM account_data WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not ad_row:
        return jsonify({"error": "not found"}), 404

    data   = decrypt_account_data(uid, ad_row["data_enc"] or "")
    items  = data.get("items") or data.get("ai_items") or []
    confidences = [i["confidence"] for i in items if isinstance(i.get("confidence"), (int, float))]
    sources_breakdown = {
        "api":   sum(1 for i in items if i.get("from_api")),
        "dom":   sum(1 for i in items if not i.get("from_api")),
    }
    coverage = _coverage_score(source, len(items))
    gaps = _coverage_gaps(source, [f.get("key","") for f in items])
    gap_info = {
        "count": len(gaps),
        "labels": [desc for _, desc in gaps[:3]],
        "more": max(0, len(gaps) - 3),
    }

    # Recent changes
    recent_changes = db.execute(
        "SELECT field_label, old_value, new_value, changed_at FROM field_history "
        "WHERE user_id=? AND source=? ORDER BY changed_at DESC LIMIT 5",
        (uid, source)
    ).fetchall()

    return jsonify({
        "source":           source,
        "synced_at":        ad_row["synced_at"],
        "sync_status":      data.get("sync_status", "ok"),
        "failure_reason":   ad_row["sync_failure_reason"],
        "field_count":      len(items),
        "gaps":            gap_info,
        "confidence_avg":   round(sum(confidences) / len(confidences), 2) if confidences else None,
        "confidence_min":   round(min(confidences), 2) if confidences else None,
        "sources":          sources_breakdown,
        "coverage":         coverage,
        "recent_changes":   [dict(r) for r in recent_changes],
        "path_failures":    db.execute(
            "SELECT SUM(failure_count) FROM site_paths WHERE site=?", (source,)
        ).fetchone()[0] or 0,
    })


@app.route("/api/coverage/<source>")
@require_login_or_key
def api_account_coverage(source):
    """
    Returns coverage score, found fields, gaps, and suggested crawl targets.
    Used by extension to decide whether to keep crawling.
    """
    uid = getattr(g, "api_key_user_id", None) or session.get("user_id")
    # Validate source key — alphanumeric + underscore, max 50 chars
    if not source or len(source) > 50 or not re.match(r'^[a-z0-9_]+$', source):
        return jsonify({"error": "invalid source"}), 400
    db = get_db()

    ad_row = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?", (uid, source)
    ).fetchone()
    if not ad_row:
        return jsonify({"coverage_pct": 0, "gaps": [], "targets": [], "found_count": 0})

    d = decrypt_account_data(uid, ad_row["data_enc"] or "")
    items = d.get("items") or d.get("ai_items") or []

    cat = _source_category(source)
    cat_key = None
    for ck, schema in _CATEGORY_SCHEMAS.items():
        if source in schema.get("sources", set()):
            cat_key = ck
            break
    expected = _EXPECTED_FIELDS.get(cat_key or "", {})
    found_keys = [it.get("key", "") for it in items]

    gaps = _coverage_gaps(source, found_keys)
    targets = _generate_gap_targets(source, found_keys, items)

    coverage_pct = 0
    if expected:
        found_count = max(0, len(expected) - len(gaps))
        coverage_pct = int(found_count / len(expected) * 100)
    else:
        found_count = len(items)
        coverage_pct = min(100, found_count * 15)  # rough estimate when no schema

    return jsonify({
        "source": source,
        "category": cat,
        "coverage_pct": coverage_pct,
        "found_count": len(items),
        "expected_count": len(expected),
        "gaps": [{"key": gk, "description": gdesc} for gk, gdesc in gaps],
        "targets": targets,  # path keywords to hunt for
        "should_continue": coverage_pct < 70 and len(gaps) > 0,
    })


@app.route("/api/my-key")
@require_login
def api_my_key():
    """Return the current user's API key — used by the Settings page Reveal/Copy buttons.
    Kept behind session auth so the key never lives in a DOM attribute."""
    user = get_db().execute("SELECT api_key FROM users WHERE id=?", (session["user_id"],)).fetchone()
    return jsonify({"key": user["api_key"] if user else ""})


@app.route("/api/latest-sync")
@require_login
def api_latest_sync():
    """Return the most recent synced_at timestamp across all the user's accounts.

    The dashboard polls this to detect when a sync finishes — much more reliable
    than the fragile postMessage relay approach.
    """
    uid = session["user_id"]
    db  = get_db()
    row = db.execute(
        "SELECT MAX(synced_at) AS ts FROM account_data WHERE user_id=?",
        (uid,)
    ).fetchone()
    return jsonify({"latest": row["ts"] if row else None})


@app.route("/api/sync/finalize", methods=["POST"])
def api_sync_finalize():
    """Called by the extension when a full sync session completes.
    Sets synced_at to the session timestamp for all accounts synced in the last 30 minutes,
    so every card shows the same 'Synced X ago' time on the dashboard.
    Auth: X-Mighty-Key header.
    """
    user, body = api_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401

    _raw_ts = (body or {}).get("session_ts") or ""
    if _raw_ts and len(_raw_ts) >= 10 and _raw_ts[4:5] == "-" and _raw_ts[7:8] == "-":
        session_ts = _raw_ts[:26]
    else:
        session_ts = iso()
    sources    = (body or {}).get("sources")  # optional list to restrict which accounts
    db = get_db()
    # Only touch accounts that were actually synced in this session window (last 30 min),
    # not accounts that haven't been synced in days.
    cutoff = (
        __import__("datetime").datetime.utcnow()
        - __import__("datetime").timedelta(minutes=30)
    ).isoformat()
    if sources and isinstance(sources, list):
        sources = sources[:50]  # cap to prevent oversized IN clause
        placeholders = ",".join("?" * len(sources))
        result = db.execute(
            f"UPDATE account_data SET synced_at=? WHERE user_id=? AND source IN ({placeholders})",
            [session_ts, user["id"]] + sources
        )
    else:
        result = db.execute(
            "UPDATE account_data SET synced_at=? WHERE user_id=? AND synced_at >= ?",
            (session_ts, user["id"], cutoff)
        )
    db.commit()
    print(f"[Finalize] Unified {result.rowcount} accounts to session_ts={session_ts[:19]}", flush=True)
    # Tell the dashboard's auto-sync check that a fresh sync just landed so it
    # doesn't immediately re-trigger cloudSync() on the next page load.
    _sync_status[user["id"]] = {"running": False, "last": session_ts}
    return jsonify({"ok": True, "updated": result.rowcount, "session_ts": session_ts})



@app.route("/api/sync/failure", methods=["POST"])
def api_sync_failure():
    """Called by the extension when an account sync attempt fails.
    Records the failure reason on the account_data row so the dashboard
    can show an actionable message (e.g. 'Log back in', 'Timed out').
    Only updates existing records — won't create a new row.
    Auth: X-Mighty-Key header.
    """
    user, body = api_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401
    uid    = user["id"]
    source = (body or {}).get("source", "").strip()
    reason = (body or {}).get("reason", "").strip()
    # Map extension reason codes to known SYNC_FAILURE_MESSAGES keys
    _reason_map = {
        "no_data":    "no_data",
        "no_content": "no_data",
        "timeout":    "timeout",
        "login_wall": "login_required",
        "login_required": "login_required",
    }
    reason = _reason_map.get(reason, "no_data")
    if not source:
        return jsonify({"error": "source required"}), 400
    db  = get_db()
    row = db.execute(
        "SELECT id, data_enc FROM account_data WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not row:
        # Account never synced successfully — nothing to update
        return jsonify({"ok": True, "updated": False, "note": "no existing record"})
    payload = decrypt_account_data(uid, row["data_enc"] or "")
    payload["sync_status"]         = "login_required" if reason == "login_required" else "no_data"
    payload["sync_failure_reason"] = reason
    now = iso()
    db.execute(
        "UPDATE account_data SET data_enc=?, sync_failure_reason=?, sync_status=?, synced_at=? WHERE user_id=? AND source=?",
        (encrypt_account_data(uid, payload), reason, payload["sync_status"], now, uid, source)
    )
    db.commit()
    print(f"[SyncFailure] uid={uid} source={source} reason={reason}", flush=True)
    return jsonify({"ok": True, "updated": True})


@app.route("/api/reclassify", methods=["POST"])
@require_login
def api_reclassify():
    """Backfill _type on all existing account_data items for this user."""
    uid  = session["user_id"]
    db   = get_db()
    rows = db.execute("SELECT source, data_enc FROM account_data WHERE user_id=?", (uid,)).fetchall()
    updated = 0
    for row in rows:
        data  = decrypt_account_data(uid, row["data_enc"] or "")
        items = data.get("items") or data.get("ai_items") or []
        changed = False
        for item in items:
            t = classify_benefit(item.get("label",""), str(item.get("value","")), row["source"])
            if item.get("_type") != t:
                item["_type"] = t; changed = True
        if changed:
            data["items"] = items
            db.execute("UPDATE account_data SET data_enc=? WHERE user_id=? AND source=?",
                       (encrypt_account_data(uid, data), uid, row["source"]))
            updated += 1
    db.commit()
    return jsonify({"ok": True, "accounts_updated": updated})


@app.route("/api/extension/capture", methods=["POST"])
def api_extension_capture():
    """Receive a page captured by the extension for a custom (user-defined) account.
    Creates the account if it doesn't exist, updates page data, triggers AI discovery.
    Auth: X-Mighty-Key header.
    """
    user, _ = api_user()
    if not user:
        return jsonify({"error": "unauthorized"}), 401

    body = request.get_json(silent=True) or {}
    name     = (body.get("name") or "").strip()
    category = (body.get("category") or "Other").strip()
    url      = (body.get("url") or "").strip()
    raw_text = body.get("raw_text") or ""
    synced_at = body.get("synced_at") or iso()

    if not name:
        return jsonify({"error": "name required"}), 400

    # Stable source key derived from name
    source = "custom_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:30]
    uid    = user["id"]
    db     = get_db()

    # Read or create account_credentials row
    cred_row = db.execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()

    if cred_row:
        try:    ex = json.loads(decrypt_cred(uid, cred_row["extra_enc"] or "") or "{}")
        except: ex = {}
        urls = ex.get("urls", [])
        if url and url not in urls:
            urls.append(url)
        ex.update({"custom": True, "display_name": name, "category": category, "urls": urls})
        db.execute(
            "UPDATE account_credentials SET extra_enc=?, updated_at=? WHERE user_id=? AND source=?",
            (encrypt_cred(uid, json.dumps(ex)), iso(), uid, source)
        )
    else:
        ex = {"custom": True, "display_name": name, "category": category, "urls": [url] if url else []}
        db.execute(
            "INSERT INTO account_credentials (user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (uid, source, "", "", encrypt_cred(uid, json.dumps(ex)), iso(), iso())
        )

    # Upsert account_data
    data_payload = {
        "sync_status": "ok" if raw_text else "no_data",
        "sync_source": "extension",
        "items": [],
        "raw_text": raw_text,
    }
    enc = encrypt_account_data(uid, data_payload)
    existing = db.execute(
        "SELECT id FROM account_data WHERE user_id=? AND source=?", (uid, source)
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE account_data SET data_enc=?, synced_at=? WHERE user_id=? AND source=?",
            (enc, synced_at, uid, source)
        )
    else:
        db.execute(
            "INSERT INTO account_data (user_id, source, display_name, icon, color, data_enc, synced_at) VALUES (?,?,?,?,?,?,?)",
            (uid, source, name, "", "", enc, synced_at)
        )
    db.commit()

    # Persist action items from this extension capture
    try:
        populate_action_items(uid, source, data_payload)
    except Exception as _pai_cap_err:
        print(f"[Mighty] populate_action_items (capture) error: {_pai_cap_err}", flush=True)

    # Trigger AI field discovery in background
    if raw_text and _claude:
        def _discover():
            # Run entirely inside app context — get_db() and hint-phrase loading
            # both require it; do NOT capture db from the outer request scope.
            with app.app_context():
                _db_d = get_db()
                fields = claude_discover_fields(raw_text, name, source=source)
                if fields:
                    _save_discovered_fields(uid, source, fields)
                else:
                    _cred = _db_d.execute(
                        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
                        (uid, source)
                    ).fetchone()
                    if _cred:
                        try:
                            _ex = json.loads(decrypt_cred(uid, _cred["extra_enc"] or "") or "{}")
                            _ex["discovery_failed"] = True
                            _db_d.execute(
                                "UPDATE account_credentials SET extra_enc=? WHERE user_id=? AND source=?",
                                (encrypt_cred(uid, json.dumps(_ex)), uid, source)
                            )
                            _db_d.commit()
                        except Exception:
                            pass
        threading.Thread(target=_discover, daemon=True).start()

    return jsonify({"ok": True, "source": source})


@app.route("/api/extension/accounts", methods=["GET"])
def api_extension_accounts():
    """Return connected accounts with metadata for the Chrome extension.

    The extension calls this to discover which accounts to sync and what
    name/icon/color to use when pushing results back via /api/data/sync.
    Auth: X-API-Key header.
    """
    user, _ = api_user()
    if not user:
        return jsonify({"error": "Invalid or missing api_key"}), 401

    db = get_db()
    rows = db.execute(
        "SELECT source, username_enc FROM account_credentials WHERE user_id=?",
        (user["id"],)
    ).fetchall()

    # Site metadata: name, icon, color — kept in sync with scrape.py SITE_CFGS
    SITE_META = {
        "southwest":    ("Southwest",          "✈️",  "#fef3c7"),
        "united":       ("United Airlines",    "✈️",  "#eff6ff"),
        "american_air": ("American Airlines",  "✈️",  "#fce7f3"),
        "alaska_air":   ("Alaska Airlines",    "✈️",  "#ecfdf5"),
        "delta":        ("Delta",              "✈️",  "#eff6ff"),
        "amex":         ("American Express",   "💳", "#e8f0fe"),
        "chase":        ("Chase",              "💳", "#fef3c7"),
        "wells_fargo":  ("Wells Fargo",        "🏦", "#fef3c7"),
        "bofa":         ("Bank of America",    "🏦", "#fee2e2"),
        "capital_one":  ("Capital One",        "💳", "#fce7f3"),
        "discover":     ("Discover",           "💳", "#fff7ed"),
        "citi":         ("Citi",               "💳", "#ecfdf5"),
        "paypal":       ("PayPal",             "💰", "#eff6ff"),
        "fidelity":     ("Fidelity",           "📈", "#ecfdf5"),
        "schwab":       ("Charles Schwab",     "📈", "#eff6ff"),
        "marriott":     ("Marriott Bonvoy",    "🏨", "#fff7ed"),
        "hilton":       ("Hilton Honors",      "🏨", "#eff6ff"),
        "hyatt":        ("Hyatt",              "🏨", "#f5f3ff"),
        "ihg":          ("IHG",                "🏨", "#fff7ed"),
        "wyndham":      ("Wyndham",            "🏨", "#fce7f3"),
        "amazon":       ("Amazon",             "📦", "#fff7ed"),
        "target":       ("Target",             "🎯", "#fee2e2"),
        "costco":       ("Costco",             "🛒", "#eff6ff"),
        "netflix":      ("Netflix",            "🎬", "#fee2e2"),
        "hulu":         ("Hulu",               "📺", "#ecfdf5"),
        "spotify":      ("Spotify",            "🎵", "#ecfdf5"),
        "disney_plus":  ("Disney+",            "🎬", "#eff6ff"),
        "att":          ("AT&T",               "📱", "#eff6ff"),
        "verizon":      ("Verizon",            "📱", "#fce7f3"),
        "tmobile":      ("T-Mobile",           "📱", "#fce7f3"),
        "xfinity":      ("Xfinity",            "📡", "#eff6ff"),
        "hertz":        ("Hertz",              "🚗", "#fef3c7"),
        "cvs":          ("CVS",                "💊", "#fee2e2"),
        "walgreens":    ("Walgreens",          "💊", "#ecfdf5"),
        "max":             ("Max",                  "🎬", "#002be7"),
        "peacock":         ("Peacock",               "🦚", "#ffd700"),
        "paramount_plus":  ("Paramount+",            "🎬", "#0064ff"),
        "ticketmaster":    ("Ticketmaster",          "🎟️", "#026cdf"),
        "walmart":         ("Walmart",               "🛒", "#0071ce"),
        "starbucks":       ("Starbucks",             "☕", "#00704a"),
        "state_farm":      ("State Farm",            "🛡️", "#e8000d"),
        "kaiser":          ("Kaiser Permanente",     "🏥", "#006ba6"),
        "att_wireless":    ("AT&T Wireless",         "📱", "#00a8e0"),
        "pa_utilities":    ("Palo Alto Utilities",   "⚡", "#4b9cd3"),
    }

    accounts = []
    for row in rows:
        source = row["source"]
        if source == "_email":
            continue
        meta = SITE_META.get(source)
        if not meta:
            continue

        name, icon, color = meta
        ad_row = db.execute(
            "SELECT entry_url, synced_at FROM account_data WHERE user_id=? AND source=?",
            (user["id"], source)
        ).fetchone()
        entry_url = ad_row["entry_url"] if ad_row and ad_row["entry_url"] else None
        synced_at = ad_row["synced_at"] if ad_row else None
        accounts.append({"source": source, "name": name, "icon": icon, "color": color,
                         "entry_url": entry_url, "synced_at": synced_at})

    return jsonify(accounts)


@app.route("/api/data", methods=["GET", "POST"])
@require_login
def api_data_get():
    """Return all decrypted account data for the logged-in user (dashboard use)."""
    rows = get_db().execute(
        "SELECT * FROM account_data WHERE user_id=? ORDER BY synced_at DESC",
        (session["user_id"],),
    ).fetchall()
    result = {}
    for row in rows:
        data = decrypt_account_data(session["user_id"], row["data_enc"] or "")
        result[row["source"]] = {
            "display_name": row["display_name"],
            "icon":         row["icon"],
            "color":        row["color"],
            "synced_at":    row["synced_at"],
            "status":       data.get("status", "unknown"),
            "items":        data.get("items", []),
        }
    return jsonify({"ok": True, "data": result})


# ── 2FA pending approval ──────────────────────────────────────────────────────

@app.route("/api/2fa/request", methods=["POST"])
def api_2fa_request():
    """Scraper calls this when it hits an SMS/push 2FA challenge it can't auto-handle."""
    user, body = api_user()
    if not user:
        return jsonify({"ok": False, "error": "Invalid api_key"}), 401

    source         = body.get("source", "")
    account_name   = body.get("account_name", source)
    challenge_type = body.get("challenge_type", "sms")   # sms | push
    message        = body.get("message", "")

    # Expire any existing pending challenge for this source
    get_db().execute(
        "UPDATE pending_2fa SET status='expired' WHERE user_id=? AND source=? AND status='pending'",
        (user["id"], source)
    )

    challenge_id = secrets.token_hex(16)
    now          = utcnow()
    expires      = (now + timedelta(minutes=10)).isoformat()
    get_db().execute(
        "INSERT INTO pending_2fa (id,user_id,source,account_name,challenge_type,message,status,created_at,expires_at) "
        "VALUES (?,?,?,?,?,?,'pending',?,?)",
        (challenge_id, user["id"], source, account_name, challenge_type, message, now.isoformat(), expires)
    )
    get_db().commit()

    # Notify user by email — fire in background so the scraper gets its
    # response immediately rather than waiting up to 10 s on Postmark.
    _email_args = (user["email"], account_name, challenge_type, message,
                   f"{base_url()}/dashboard#2fa-{challenge_id}")
    threading.Thread(target=_send_2fa_email, args=_email_args, daemon=True).start()

    return jsonify({"ok": True, "challenge_id": challenge_id})


@app.route("/api/2fa/poll/<challenge_id>")
def api_2fa_poll(challenge_id):
    """Scraper polls this until status becomes 'resolved'."""
    user, _ = api_user()
    if not user:
        return jsonify({"ok": False, "error": "Invalid api_key"}), 401

    row = get_db().execute(
        "SELECT * FROM pending_2fa WHERE id=? AND user_id=?",
        (challenge_id, user["id"])
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "Challenge not found"}), 404

    # Auto-expire
    if row["status"] == "pending" and row["expires_at"] < utcnow().isoformat():
        get_db().execute("UPDATE pending_2fa SET status='expired' WHERE id=?", (challenge_id,))
        get_db().commit()
        return jsonify({"ok": True, "status": "expired"})

    return jsonify({
        "ok":     True,
        "status": row["status"],
        "code":   row["code"] or "",
    })


@app.route("/api/2fa/respond/<challenge_id>", methods=["POST"])
@require_login
def api_2fa_respond(challenge_id):
    """User submits their SMS code or push confirmation via the dashboard."""
    check_csrf()
    code = request.form.get("code", "").strip()
    confirmed = request.form.get("confirmed", "")

    row = get_db().execute(
        "SELECT * FROM pending_2fa WHERE id=? AND user_id=? AND status='pending'",
        (challenge_id, session["user_id"])
    ).fetchone()
    if not row:
        return jsonify({"ok": False, "error": "Challenge not found or already resolved"}), 404

    get_db().execute(
        "UPDATE pending_2fa SET status='resolved', code=? WHERE id=?",
        (code or confirmed or "confirmed", challenge_id)
    )
    get_db().commit()
    return jsonify({"ok": True})


@app.route("/api/2fa/pending")
@require_login
def api_2fa_pending():
    """Return active pending 2FA challenges for the logged-in user."""
    rows = get_db().execute(
        "SELECT * FROM pending_2fa WHERE user_id=? AND status='pending' ORDER BY created_at DESC",
        (session["user_id"],)
    ).fetchall()
    # Auto-expire old ones
    now = utcnow().isoformat()
    challenges = []
    for r in rows:
        if r["expires_at"] < now:
            get_db().execute("UPDATE pending_2fa SET status='expired' WHERE id=?", (r["id"],))
        else:
            challenges.append({
                "id": r["id"], "source": r["source"],
                "account_name": r["account_name"],
                "challenge_type": r["challenge_type"],
                "message": r["message"] or "",
                "expires_at": r["expires_at"],
            })
    if any(r["expires_at"] < now for r in rows):
        get_db().commit()
    return jsonify({"ok": True, "challenges": challenges})


def _send_2fa_email(to_email: str, account_name: str, challenge_type: str,
                    message: str, dashboard_url: str) -> None:
    if not POSTMARK_API_KEY:
        print(f"[2FA] Email skipped (no Postmark): {account_name} needs {challenge_type}", flush=True)
        return
    subject = f"Action needed: 2FA required for {account_name}"
    body_html = f"""<div style="font-family:Arial,sans-serif;max-width:480px;margin:0 auto;padding:24px">
<h2 style="color:#7c3aed">⚡ Mighty needs your help</h2>
<p>Your <strong>{account_name}</strong> account requires {"a verification code" if challenge_type=="sms" else "push approval"} to sync.</p>
{"<p style='color:#555'>" + message + "</p>" if message else ""}
<a href="{dashboard_url}" style="display:inline-block;margin-top:16px;padding:12px 24px;background:#7c3aed;color:#fff;border-radius:8px;text-decoration:none;font-weight:600">Enter code in Mighty →</a>
<p style="color:#9ca3af;font-size:12px;margin-top:24px">This request expires in 10 minutes.</p>
</div>"""
    payload = json.dumps({
        "From": POSTMARK_FROM, "To": NOTIFY_EMAIL_OVERRIDE or to_email,
        "Subject": subject, "HtmlBody": body_html,
    }).encode()
    req = urllib.request.Request(
        "https://api.postmarkapp.com/email",
        data=payload,
        headers={"Content-Type": "application/json", "X-Postmark-Server-Token": POSTMARK_API_KEY},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception as e:
        print(f"[2FA] Email failed: {e}", flush=True)


# ── Cloud sync ────────────────────────────────────────────────────────────────

_sync_status: dict = {}   # user_id → {"running": bool, "last": iso, "result": dict}

def _cloud_sync_user(user_id: str, api_key: str, mighty_url: str, force: bool = False) -> dict:
    """Run scrapers for one user server-side and return result."""
    try:
        import scrape as _scrape
        result = _scrape.run_sync(
            api_key=api_key,
            mighty_url=mighty_url,
            log=lambda m: print(f"[CloudSync:{user_id[:6]}] {m}", flush=True),
            force=force,
        )
        _sync_status[user_id] = {
            "running": False, "last": iso(),
            "synced": result.get("synced", 0),
            "errors": result.get("errors", 0),
        }
        return result
    except Exception as e:
        _sync_status[user_id] = {"running": False, "last": iso(), "error": str(e)[:120]}
        raise

def _auto_discover_missing(uid: str) -> None:
    """Run field discovery for any connected account that has raw_text.
    Skips accounts whose raw_text hasn't changed since last discovery (hash check).
    Always establishes its own app context so it is safe to call from any thread."""
    if not _claude:
        return
    with app.app_context():
        try:
            import hashlib
            cred_rows = get_db().execute(
                "SELECT source, extra_enc FROM account_credentials WHERE user_id=?", (uid,)
            ).fetchall()

            def _discover_one(cr):
                # Each thread pool thread needs its own app context.
                with app.app_context():
                    try:
                        src = cr["source"]
                        if src.startswith("_"):
                            return
                        ex: dict = {}
                        if cr["extra_enc"]:
                            try: ex = json.loads(decrypt_cred(uid, cr["extra_enc"]))
                            except Exception: pass
                        try:
                            ad = get_db().execute(
                                "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
                                (uid, src)
                            ).fetchone()
                        except Exception:
                            return
                        if not ad:
                            return
                        raw_text = decrypt_account_data(uid, ad["data_enc"] or "").get("raw_text", "")
                        if not raw_text:
                            return
                        # Skip if raw_text is identical to last discovery run —
                        # UNLESS existing fields contain login-wall values
                        _BAD_VALUES = ("log in", "sign in", "login to", "sign in to", "no match found")
                        existing_fields = ex.get("discovered_fields", [])
                        has_bad_fields = any(
                            any(bad in str(f.get("value", "")).lower() for bad in _BAD_VALUES)
                            for f in existing_fields
                        )
                        raw_hash = hashlib.md5(raw_text.encode()).hexdigest()
                        if ex.get("last_raw_hash") == raw_hash and existing_fields and not has_bad_fields:
                            return
                        if has_bad_fields:
                            print(f"[AutoDiscover] {src}: forcing re-discovery (stale login-wall fields)", flush=True)
                        site_name = next((n for k, n, *_ in SUPPORTED_SITES if k == src), src)
                        print(f"[AutoDiscover] {src} for user {uid[:6]} (hash {raw_hash[:8]})", flush=True)
                        merged: dict = {}
                        for f in claude_discover_fields(raw_text, site_name, source=src):
                            k = f.get("key", "")
                            if k and k not in merged: merged[k] = f
                            elif k: merged[k]["value"] = f.get("value", "")
                        if merged:
                            _save_discovered_fields(uid, src, list(merged.values()))
                            # Store hash so we don't re-discover unchanged pages
                            try:
                                cred_row2 = get_db().execute(
                                    "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
                                    (uid, src)
                                ).fetchone()
                                if cred_row2 and cred_row2["extra_enc"]:
                                    ex2 = json.loads(decrypt_cred(uid, cred_row2["extra_enc"]))
                                    ex2["last_raw_hash"] = raw_hash
                                    get_db().execute(
                                        "UPDATE account_credentials SET extra_enc=? WHERE user_id=? AND source=?",
                                        (encrypt_cred(uid, json.dumps(ex2)), uid, src)
                                    )
                                    get_db().commit()
                            except Exception:
                                pass
                    except Exception as e:
                        print(f"[AutoDiscover] Error in {cr.get('source', '?')}: {e}", flush=True)

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = [executor.submit(_discover_one, cr) for cr in cred_rows]
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[AutoDiscover] Thread error: {e}", flush=True)
        except Exception as e:
            print(f"[AutoDiscover] Error: {e}", flush=True)


def _cloud_sync_all():
    """Sync all users who have credentials configured."""
    with app.app_context():
        try:
            url = os.environ.get("BASE_URL", "https://mighty-selfserve-production.up.railway.app")
            # Single query: only users that have at least one credential row.
            # Avoids an N+1 COUNT query per user.
            rows = get_db().execute(
                "SELECT DISTINCT u.id, u.api_key FROM users u "
                "WHERE EXISTS (SELECT 1 FROM account_credentials ac WHERE ac.user_id = u.id)"
            ).fetchall()
            for row in rows:
                uid = row["id"]
                if _sync_status.get(uid, {}).get("running"):
                    continue
                _sync_status[uid] = {"running": True}
                try:
                    _cloud_sync_user(uid, row["api_key"], url)
                    # Auto-discover fields for any account that doesn't have them yet
                    _auto_discover_missing(uid)
                except Exception as e:
                    print(f"[AutoSync] User {uid[:6]} error: {e}", flush=True)
        except Exception as e:
            print(f"[AutoSync] Loop error: {e}", flush=True)

def _start_cloud_scheduler():
    interval = int(os.environ.get("SYNC_INTERVAL_HOURS", "1"))
    def loop():
        import time
        time.sleep(120)  # Wait 2 min after startup before first sync
        while True:
            print(f"[AutoSync] Running scheduled sync for all users", flush=True)
            _cloud_sync_all()
            time.sleep(interval * 3600)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    print(f"[Mighty] Cloud sync scheduler started (every {interval}h)", flush=True)


@app.route("/sync/now", methods=["POST"])
@require_login
def sync_now_cloud():
    """Trigger an immediate cloud sync for the current user (all accounts)."""
    check_csrf()
    uid = session["user_id"]
    if _sync_status.get(uid, {}).get("running"):
        return jsonify({"ok": False, "error": "Sync already in progress"}), 409
    user = get_db().execute("SELECT api_key FROM users WHERE id=?", (uid,)).fetchone()
    url  = os.environ.get("BASE_URL", "https://mighty-selfserve-production.up.railway.app")
    _sync_status[uid] = {"running": True}
    # Snapshot the api_key string so the thread doesn't hold a sqlite3.Row
    # reference across a potential db connection teardown.
    _api_key = user["api_key"]

    def _do():
        with app.app_context():
            try:
                _cloud_sync_user(uid, _api_key, url, force=True)
                _auto_discover_missing(uid)  # discover fields for any account that needs it
            except Exception as e:
                print(f"[SyncNow] {e}", flush=True)
            finally:
                _sync_status[uid] = {"running": False, "last": iso()}
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True, "message": "Sync started"})


@app.route("/sync/account/<source>", methods=["POST"])
@require_login
def sync_account_cloud(source):
    """Trigger an immediate cloud sync for a single account."""
    check_csrf()
    uid  = session["user_id"]
    # Validate source is a known site key to prevent log injection / junk sync entries
    _valid_sources = {s[0] for s in SUPPORTED_SITES}
    if source not in _valid_sources:
        return jsonify({"ok": False, "error": "Unknown source"}), 400
    if _sync_status.get(uid, {}).get("running"):
        return jsonify({"ok": False, "error": "Sync already in progress"}), 409
    user = get_db().execute("SELECT api_key FROM users WHERE id=?", (uid,)).fetchone()
    url  = os.environ.get("BASE_URL", "https://mighty-selfserve-production.up.railway.app")
    _sync_status[uid] = {"running": True, "step": "connecting", "source": source}
    _api_key = user["api_key"]  # snapshot before thread to avoid stale sqlite3.Row

    def _do():
        with app.app_context():
            try:
                import scrape as _scrape
                _sync_status[uid] = {"running": True, "step": "scraping", "source": source}
                result = _scrape.run_sync(
                    api_key=_api_key,
                    mighty_url=url,
                    log=lambda m: print(f"[SyncAccount:{source}] {m}", flush=True),
                    only_source=source,
                )
                # Auto-discover fields after sync — no manual step needed
                fields_found = 0
                if result.get("synced", 0) > 0 and _claude:
                    try:
                        _sync_status[uid] = {"running": True, "step": "discovering", "source": source}
                        ad = get_db().execute(
                            "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
                            (uid, source)
                        ).fetchone()
                        if ad:
                            ad_data = decrypt_account_data(uid, ad["data_enc"] or "")
                            raw_text = ad_data.get("raw_text", "")
                            site_name = next(
                                (n for k, n, *_ in SUPPORTED_SITES if k == source), source
                            )
                            if raw_text:
                                # Single discovery call (LLM is deterministic at temp=0; 3x was redundant)
                                fields = list(claude_discover_fields(raw_text, site_name, source=source))
                                if fields:
                                    _save_discovered_fields(uid, source, fields)
                                    fields_found = len(fields)
                    except Exception as de:
                        print(f"[AutoDiscover:{source}] {de}", flush=True)
                _sync_status[uid] = {
                    "running": False, "last": iso(),
                    "step": "done",
                    "source": source,
                    "synced": result.get("synced", 0),
                    "errors": result.get("errors", 0),
                    "fields_found": fields_found,
                }
            except Exception as e:
                _sync_status[uid] = {"running": False, "last": iso(), "step": "error",
                                     "source": source, "error": str(e)[:120]}
                print(f"[SyncAccount] {e}", flush=True)
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/sync/status")
@require_login
def sync_status():
    """Return current sync status for the logged-in user."""
    uid    = session["user_id"]
    status = _sync_status.get(uid, {})
    # _sync_status is in-memory and lost on worker restart / multi-instance deploys.
    # Fall back to the DB so the dashboard auto-sync check sees a real last-sync time
    # even after Railway restarts or when the extension synced via a different worker.
    if not status.get("last"):
        row = get_db().execute(
            "SELECT MAX(synced_at) AS ts FROM account_data WHERE user_id=?", (uid,)
        ).fetchone()
        if row and row["ts"]:
            status = {"running": status.get("running", False), "last": row["ts"]}
    return jsonify({"ok": True, **status})


# ── Error handlers ────────────────────────────────────────────────────────────

@app.errorhandler(403)
def forbidden(e):
    if request.path.startswith("/api/") or request.path.startswith("/credentials/"):
        return jsonify({"ok": False, "error": "Session expired — please refresh the page"}), 403
    return NOT_FOUND_HTML.replace("Page not found", "Forbidden").replace(
        "The page you were looking for doesn't exist.", "Access denied."), 403

@app.errorhandler(404)
def not_found(e):
    return NOT_FOUND_HTML, 404

@app.errorhandler(500)
def server_error(e):
    return NOT_FOUND_HTML.replace("Page not found", "Something went wrong").replace("The page you were looking for doesn't exist.", "An unexpected error occurred. Please try again or <a href=\"/\">return home</a>."), 500




@app.route("/api/benefits/relevant")
@require_login_or_key
def api_benefits_relevant():
    """
    Returns benefits relevant to the user's current intent.
    Query params:
      context: 'flight' | 'hotel' | 'car' | 'shopping' | 'dining'
      url: current page URL (used for logging/debugging, not matched)
    """
    uid = get_current_user_id()
    context = request.args.get("context", "").lower().strip()

    if context not in _BENEFIT_APPLICABILITY:
        return jsonify({"context": context, "benefits": [], "count": 0})

    relevant_keys = _BENEFIT_APPLICABILITY[context]

    # Scan all account data for matching fields
    rows = get_db().execute(
        "SELECT source, display_name, data_enc, synced_at FROM account_data WHERE user_id=?", (uid,)
    ).fetchall()

    # Build synced_ago lookup per source
    import datetime as _dt_ev
    def _synced_ago(synced_at_str):
        if not synced_at_str:
            return ""
        try:
            _dt = _dt_ev.datetime.fromisoformat(synced_at_str.replace("Z",""))
            _delta = _dt_ev.datetime.utcnow() - _dt
            _h = int(_delta.total_seconds() / 3600)
            if _h < 1:   return "just now"
            if _h < 24:  return f"{_h}h ago"
            return f"{_h // 24}d ago"
        except Exception:
            return ""

    # Why this item was surfaced (context-based)
    _WHY_SHOWN = {
        "flight":   "You're viewing flights",
        "hotel":    "You're viewing hotels",
        "car":      "You're renting a car",
        "shopping": "You're shopping",
        "dining":   "You're browsing dining",
    }
    _why_shown = _WHY_SHOWN.get(context, "Relevant to your current activity")

    benefits = []
    for row in rows:
        try:
            data = decrypt_account_data(uid, row["data_enc"] or "")
            items = data.get("items", []) or data.get("ai_items", []) or []
        except Exception:
            continue

        for it in items:
            fk = (it.get("key") or "").lower()
            fl = (it.get("label") or "").lower()
            fv = str(it.get("value") or "")

            # Skip empty, zero, or obviously irrelevant values
            if not fv or fv.lower() in ("0", "none", "n/a", "unknown", ""):
                continue

            # Match against relevant keys
            matched = any(
                rk in fk or rk in fl
                for rk in relevant_keys
            )
            if matched:
                score, factors = _relevance_score(
                    it.get("key", ""), it.get("label", ""), fv, context=context
                )
                # Confidence from score: items directly scraped are inherently high confidence
                _src_type = "ai_items" if "ai_items" in data and it in data.get("ai_items",[]) else "scraped"
                _conf = "high" if (score >= 0.65 and _src_type == "scraped") else \
                        "medium" if score >= 0.40 else "low"
                benefits.append({
                    "account":    row["display_name"],
                    "source":     row["source"],
                    "field_key":  it.get("key", ""),
                    "label":      it.get("label", ""),
                    "value":      fv,
                    "synced_ago": _synced_ago(row["synced_at"] if "synced_at" in row.keys() else ""),
                    "confidence": _conf,
                    "why_shown":  _why_shown,
                    "_score":     score,
                    "_why":       factors,
                })

    # Deduplicate by (account, label), sort by relevance score, cap at 8
    seen = set()
    unique_benefits = []
    for b in sorted(benefits, key=lambda x: -x["_score"]):
        key = (b["account"], b["label"])
        if key not in seen:
            seen.add(key)
            unique_benefits.append({k: v for k, v in b.items() if k not in ("_score",)})
    unique_benefits = unique_benefits[:8]

    # Load feedback signals: suppress negatives, boost positives
    _suppressed = set()
    _positive   = set()
    for _row in get_db().execute(
        "SELECT source, field_key, feedback FROM benefit_feedback WHERE user_id=?", (uid,)
    ).fetchall():
        _key = (_row["source"], _row["field_key"])
        if _row["feedback"] in ("dont_show", "not_relevant"):
            _suppressed.add(_key)
        elif _row["feedback"] in ("useful", "already_used"):
            _positive.add(_key)

    # Filter suppressed items
    unique_benefits = [
        b for b in unique_benefits
        if (b.get("source",""), b.get("field_key","")) not in _suppressed
    ]

    # Boost positively-rated items to the top
    unique_benefits.sort(
        key=lambda b: 0 if (b.get("source",""), b.get("field_key","")) in _positive else 1
    )

    # ── Derived benefits from cross-program partnerships ───────────────────
    # Collect all items across all accounts to check for status fields
    _all_items: list[dict] = []
    _connected_srcs: list[str] = []
    for _row in rows:
        _connected_srcs.append(_row["source"])
        try:
            _d = decrypt_account_data(uid, _row["data_enc"] or "")
            _its = _d.get("items", []) or _d.get("ai_items", []) or []
            for _it in _its:
                _all_items.append({**_it, "source": _row["source"],
                                   "display_name": _row["display_name"]})
        except Exception:
            pass

    _derived = get_derived_benefits(_all_items, _connected_srcs, context)
    # Append derived items, stamping why_shown, not already covered by a direct match
    _direct_programs = {b.get("label", "").lower() for b in unique_benefits}
    for _db in _derived:
        if _db["label"].lower() not in _direct_programs:
            _db.setdefault("why_shown", _why_shown)
            unique_benefits.append(_db)

    return jsonify({
        "context": context,
        "benefits": unique_benefits,
        "count": len(unique_benefits),
    })


@app.route("/api/benefits/discover")
@require_login_or_key
def api_benefits_discover():
    """
    Returns factual card/account descriptions for accounts the user may not have,
    based on their connected accounts.  No dollar-value estimates.

    Query params:
      context  – optional: 'flight' | 'hotel' | 'car' | 'shopping' | 'dining'
                 When provided, only cards matching that context are returned.
    """
    uid     = get_current_user_id()
    context = request.args.get("context", "").lower().strip() or None
    rows    = get_db().execute(
        "SELECT source FROM account_data WHERE user_id=?", (uid,)
    ).fetchall()
    connected = [r["source"] for r in rows]
    recs = get_card_recommendations(connected, context=context)
    return jsonify({"recommendations": recs, "count": len(recs), "context": context})


@app.route("/api/settings/notifications", methods=["GET", "POST"])
@require_login_or_key
def api_settings_notifications():
    uid = get_current_user_id()
    if request.method == "GET":
        row = get_db().execute(
            "SELECT notification_pref FROM users WHERE id=?", (uid,)
        ).fetchone()
        pref = (row["notification_pref"] if row and row["notification_pref"] else "quiet")
        return jsonify({"pref": pref})
    # POST
    if not hasattr(g, "api_key_user_id"):
        check_csrf()
    data = request.get_json(silent=True) or {}
    pref = data.get("pref", "quiet")
    if pref not in ("never", "quiet", "checkout", "expiring"):
        return jsonify({"error": "invalid pref"}), 400
    get_db().execute(
        "UPDATE users SET notification_pref=? WHERE id=?", (pref, uid)
    )
    get_db().commit()
    return jsonify({"ok": True, "pref": pref})


@app.route("/api/internal/trigger-alerts", methods=["POST"])
@require_login
def api_trigger_alerts():
    """Admin/debug endpoint — trigger expiry alerts immediately for all users."""
    check_csrf()
    # Only allow admins (email ends in @anthropic.com or ADMIN_EMAIL env var matches)
    uid = session["user_id"]
    user = get_db().execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
    admin_email = os.environ.get("ADMIN_EMAIL", "")
    if not admin_email or not user or user["email"] != admin_email:
        return jsonify({"error": "forbidden"}), 403
    dry = request.args.get("dry") == "1"
    sent = run_expiry_alerts(dry_run=dry)
    return jsonify({"ok": True, "sent": sent, "dry_run": dry})


@app.route("/api/benefits/feedback", methods=["POST"])
@require_login_or_key
def api_benefits_feedback():
    """
    Record user feedback on a surfaced benefit.
    Actions: 'useful' | 'already_used' | 'not_relevant' | 'dont_show'
    'dont_show' suppresses the item from future surfacing.
    'useful' and 'already_used' are positive signals used for ranking.
    """
    if not hasattr(g, "api_key_user_id"):
        check_csrf()
    uid = get_current_user_id()
    data = request.get_json(silent=True) or {}
    source    = str(data.get("source", ""))[:100]
    field_key = str(data.get("field_key", ""))[:200]
    feedback  = str(data.get("feedback", "not_relevant"))
    context   = str(data.get("context", ""))[:50]

    if not source or not field_key:
        return jsonify({"error": "missing source or field_key"}), 400
    _valid = ("useful", "already_used", "not_relevant", "dont_show")
    if feedback not in _valid:
        feedback = "not_relevant"

    import datetime as _dt_fb
    # Upsert: replace any previous signal for the same (user, source, field_key, context)
    # so a user can change their mind (e.g. "not_relevant" → "useful")
    get_db().execute(
        "INSERT INTO benefit_feedback (user_id, source, field_key, feedback, context, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(user_id, source, field_key, context) DO UPDATE SET "
        "  feedback=excluded.feedback, created_at=excluded.created_at",
        (uid, source, field_key, feedback, context, _dt_fb.datetime.utcnow().isoformat())
    )
    get_db().commit()
    try:
        _recompute_type_affinity(uid)
    except Exception:
        pass
    return jsonify({"ok": True, "action": feedback})


@app.route("/api/benefits/correct", methods=["POST"])
@require_login
def api_benefits_correct():
    """Record a user correction for a misclassified benefit type.

    Body: { "field_key": "delta::Companion Certificate", "corrected_type": "certificate" }

    Writes to benefit_corrections (UNIQUE per user+field_key, so corrections
    can be updated).  Then re-runs populate_action_items for the affected
    source so urgency tiers reflect the corrected type immediately.
    """
    check_csrf()
    uid  = get_current_user_id()
    data = request.get_json(silent=True) or {}
    field_key      = str(data.get("field_key", ""))[:300]
    corrected_type = str(data.get("corrected_type", ""))[:50]

    # Validate
    try:
        from mighty.classify import BENEFIT_TYPES
        valid_types = set(BENEFIT_TYPES.keys())
    except Exception:
        valid_types = set()

    if not field_key:
        return jsonify({"error": "field_key required"}), 400
    if valid_types and corrected_type not in valid_types:
        return jsonify({"error": f"unknown type: {corrected_type}"}), 400

    import datetime as _dt_bc
    now = _dt_bc.datetime.utcnow().isoformat()

    get_db().execute(
        """
        INSERT INTO benefit_corrections (user_id, field_key, corrected_type, corrected_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(user_id, field_key) DO UPDATE SET
            corrected_type = excluded.corrected_type,
            corrected_at   = excluded.corrected_at
        """,
        (uid, field_key, corrected_type, now)
    )
    get_db().commit()

    # Re-populate action_items for the affected source so the corrected type
    # flows through urgency/scoring immediately.
    source = field_key.split("::")[0] if "::" in field_key else None
    if source:
        try:
            row = get_db().execute(
                "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
                (uid, source)
            ).fetchone()
            if row and row["data_enc"]:
                import json as _jbc
                _dec = decrypt_data(row["data_enc"])
                _acct_data = _jbc.loads(_dec)
                populate_action_items(uid, source, _acct_data)
        except Exception:
            pass  # best-effort; correction is already persisted

    return jsonify({"ok": True, "field_key": field_key, "corrected_type": corrected_type})


@app.route("/api/csrf-token")
@require_login
def api_csrf_token():
    """Returns CSRF token for extension POST requests."""
    return jsonify({"token": get_csrf_token()})


@app.route("/api/intent/log", methods=["POST"])
@require_login_or_key
def api_intent_log():
    """Called by extension when intent is detected and benefits are surfaced."""
    # Only require CSRF for session-based (web) requests
    if not hasattr(g, "api_key_user_id"):
        check_csrf()
    uid = get_current_user_id()
    data = request.get_json(silent=True) or {}
    intent_type   = str(data.get("intent_type", ""))[:50]
    page_url      = str(data.get("page_url", ""))[:500]
    benefits      = data.get("benefits", [])
    benefit_count = len(benefits)

    if not intent_type:
        return jsonify({"ok": False, "error": "missing intent_type"}), 400

    import json as _json
    import datetime as _dt_il
    now_iso = _dt_il.datetime.utcnow().isoformat()

    # Upsert: if same intent_type logged in last 30 min, update instead of insert
    existing = get_db().execute(
        "SELECT id FROM intent_history WHERE user_id=? AND intent_type=? "
        "AND detected_at > datetime('now','-30 minutes')",
        (uid, intent_type)
    ).fetchone()

    if existing:
        get_db().execute(
            "UPDATE intent_history SET page_url=?, benefit_count=?, benefits_json=?, detected_at=? "
            "WHERE id=?",
            (page_url, benefit_count, _json.dumps(benefits), now_iso, existing["id"])
        )
    else:
        get_db().execute(
            "INSERT INTO intent_history (user_id, intent_type, page_url, benefit_count, benefits_json, detected_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, intent_type, page_url, benefit_count, _json.dumps(benefits), now_iso)
        )
    get_db().commit()

    # Refresh rolling 30-day intent aggregates — used by Opportunity Engine for ranking
    try:
        _recompute_intent_summary(uid)
    except Exception as _ris_err:
        print(f"[Mighty] intent summary error: {_ris_err}", flush=True)

    return jsonify({"ok": True})


@app.route("/api/intent/summary")
@require_login
def api_intent_summary():
    """Return the user's pre-computed 30-day intent summary for the dashboard/engine."""
    import json as _json_is2
    uid = session["user_id"]
    row = get_db().execute("SELECT intent_summary FROM users WHERE id=?", (uid,)).fetchone()
    if row and row["intent_summary"]:
        try:
            return jsonify(_json_is2.loads(row["intent_summary"]))
        except Exception:
            pass
    # Fallback: compute on-demand
    return jsonify(_recompute_intent_summary(uid))


@app.route("/api/intent/recent")
@require_login
def api_intent_recent():
    """Returns recent intent detections for the dashboard."""
    uid = session["user_id"]
    import json as _json
    rows = get_db().execute(
        "SELECT intent_type, page_url, benefit_count, benefits_json, detected_at "
        "FROM intent_history WHERE user_id=? "
        "ORDER BY detected_at DESC LIMIT 5",
        (uid,)
    ).fetchall()

    results = []
    for r in rows:
        try:
            benefits = _json.loads(r["benefits_json"] or "[]")
        except Exception:
            benefits = []
        results.append({
            "intent_type": r["intent_type"],
            "page_url": r["page_url"],
            "benefit_count": r["benefit_count"],
            "benefits": benefits[:3],  # top 3 for display
            "detected_at": r["detected_at"],
        })

    return jsonify(results)


@app.route("/api/opportunities")
@require_login_or_key
def api_opportunities():
    """
    Returns cross-account opportunity objects for a given context.
    Used by the dashboard and (eventually) the extension.
    """
    uid     = get_current_user_id()
    context = request.args.get("context", "").strip().lower() or None
    opps    = _generate_opportunities(uid, context)
    return jsonify({"context": context, "opportunities": opps, "count": len(opps)})


# ── Email scan ────────────────────────────────────────────────────────────────

from mighty.email_scan import (
    scan_gmail, scan_imap, scan_outlook,
    get_imap_preset, CATEGORY_LABELS,
)

_EMAIL_SCAN_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Find accounts — Mighty</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:#f7f5f2;color:#1a1a1a;min-height:100vh}
.topbar{background:#fff;border-bottom:1px solid #e8e3dc;padding:0 24px;height:52px;display:flex;align-items:center;gap:12px}
.topbar a{color:#6b6b6b;text-decoration:none;font-size:13px}
.topbar a:hover{color:#1a1a1a}
.topbar .sep{color:#d1c9be}
.page{max-width:860px;margin:40px auto;padding:0 24px 60px}
h1{font-size:22px;font-weight:700;margin-bottom:6px}
.sub{color:#6b6b6b;font-size:14px;margin-bottom:32px}
.provider-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin-bottom:32px}
.provider-card{background:#fff;border:1.5px solid #e8e3dc;border-radius:12px;padding:20px;cursor:pointer;
  transition:border-color .15s,box-shadow .15s;text-align:center}
.provider-card:hover{border-color:#a8a29e;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.provider-card.active{border-color:#3b82f6;box-shadow:0 0 0 3px rgba(59,130,246,.12)}
.provider-icon{font-size:28px;margin-bottom:8px}
.provider-name{font-size:14px;font-weight:600;margin-bottom:3px}
.provider-desc{font-size:12px;color:#6b6b6b}
.imap-form{background:#fff;border:1px solid #e8e3dc;border-radius:12px;padding:24px;margin-bottom:24px;display:none}
.imap-form.visible{display:block}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.form-row.single{grid-template-columns:1fr}
label{display:block;font-size:12px;font-weight:600;color:#374151;margin-bottom:4px}
input,select{width:100%;padding:8px 10px;border:1.5px solid #d1d5db;border-radius:7px;font-size:13px;
  font-family:inherit;background:#fafafa}
input:focus,select:focus{outline:none;border-color:#3b82f6;background:#fff}
.scan-btn{background:#3b82f6;color:#fff;border:none;border-radius:8px;padding:10px 24px;
  font-size:14px;font-weight:600;cursor:pointer;font-family:inherit;transition:background .15s}
.scan-btn:hover{background:#2563eb}
.scan-btn:disabled{background:#9ca3af;cursor:not-allowed}
.status-msg{font-size:13px;color:#6b7280;margin-left:12px;display:none}
.results-section{margin-top:8px}
.results-header{font-size:16px;font-weight:700;margin-bottom:4px}
.results-sub{font-size:13px;color:#6b7280;margin-bottom:20px}
.category-block{margin-bottom:28px}
.cat-label{font-size:11px;font-weight:700;color:#9ca3af;letter-spacing:.06em;text-transform:uppercase;
  margin-bottom:10px}
.suggestion-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px}
.sugg-card{background:#fff;border:1.5px solid #e8e3dc;border-radius:10px;padding:14px 16px;
  display:flex;align-items:center;gap:12px;transition:border-color .15s}
.sugg-card.added{opacity:.65;pointer-events:none;border-color:#6ee7b7;background:#f0fdf4}
.sugg-name{flex:1;font-size:13px;font-weight:600}
.sugg-count{font-size:11px;color:#9ca3af;margin-top:1px}
.sugg-actions{display:flex;gap:6px;flex-shrink:0}
.btn-add{background:#ecfdf5;color:#059669;border:1.5px solid #6ee7b7;border-radius:6px;
  padding:5px 10px;font-size:12px;font-weight:600;cursor:pointer;font-family:inherit;
  transition:background .12s}
.btn-add:hover{background:#d1fae5}
.btn-dismiss{background:none;border:none;color:#d1d5db;font-size:16px;cursor:pointer;
  padding:4px;line-height:1;transition:color .12s}
.btn-dismiss:hover{color:#6b7280}
.btn-add-all{background:#059669;color:#fff;border:none;border-radius:6px;
  padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;
  transition:background .12s}
.btn-add-all:hover:not(:disabled){background:#047857}
.btn-add-all:disabled{opacity:.6;cursor:default}
.btn-connect{background:#1d4ed8;color:#fff;border:none;border-radius:6px;
  padding:8px 16px;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit;
  transition:background .12s,opacity .12s}
.btn-connect:hover:not(:disabled){background:#1e40af}
.btn-connect:disabled{cursor:default}
.empty-state{text-align:center;padding:48px;color:#9ca3af;font-size:14px}
.already-note{font-size:12px;color:#9ca3af;margin-top:8px;margin-bottom:12px}
.note-box{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:12px 16px;
  font-size:13px;color:#92400e;margin-bottom:24px;display:none}
.note-box.visible{display:block}
</style>
</head>
<body>
<div class="topbar">
  <a href="/dashboard">← Dashboard</a>
  <span class="sep">·</span>
  <span style="font-size:13px;font-weight:600">Find accounts from email</span>
</div>
<div class="page">
  <h1>Find accounts from your email</h1>
  <p class="sub">Mighty scans only sender addresses — never email content or attachments.</p>
  <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:13px;color:#166534;line-height:1.6">
    <strong>How Mighty uses your email:</strong> We only look at who sent you email — never what it says. This tells us which accounts you have. Your actual balances, points, and benefits are always pulled directly from each site when you connect, not from email content.
  </div>

  <div class="provider-grid" id="providerGrid">
    {gmail_card}
    {outlook_card}
    <div class="provider-card" id="card-imap" onclick="selectProvider('imap')">
      <div class="provider-icon">📬</div>
      <div class="provider-name">Any email (IMAP)</div>
      <div class="provider-desc">Gmail app password, Yahoo, iCloud, or any IMAP server</div>
    </div>
  </div>

  <div class="note-box" id="noteConfigure">
    Gmail OAuth is not configured on this server yet. Use IMAP with a Gmail App Password instead,
    or ask the server admin to set <code>GOOGLE_CLIENT_ID</code> / <code>GOOGLE_CLIENT_SECRET</code>.
  </div>

  <div class="imap-form" id="imapForm">
    <div class="form-row">
      <div>
        <label for="imapProvider">Email provider</label>
        <select id="imapProvider" onchange="onImapProviderChange()">
          <option value="gmail">Gmail (app password)</option>
          <option value="outlook">Outlook / Hotmail</option>
          <option value="yahoo">Yahoo Mail</option>
          <option value="icloud">iCloud Mail</option>
          <option value="custom">Custom IMAP server…</option>
        </select>
      </div>
      <div id="imapHostField" style="display:none">
        <label for="imapHost">IMAP host</label>
        <input type="text" id="imapHost" placeholder="imap.example.com">
      </div>
    </div>
    <div class="form-row">
      <div>
        <label for="imapUser">Email address</label>
        <input type="email" id="imapUser" placeholder="you@example.com">
      </div>
      <div>
        <label for="imapPass">Password / App password</label>
        <input type="password" id="imapPass" placeholder="••••••••••••">
      </div>
    </div>
    <button class="scan-btn" id="imapScanBtn" onclick="runImapScan()">Scan inbox</button>
    <span class="status-msg" id="imapStatus">Scanning…</span>
  </div>

  <div class="results-section" id="resultsSection" style="display:none">
    <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px">
      <div>
        <div class="results-header" id="resultsHeader"></div>
        <div class="results-sub" id="resultsSub"></div>
      </div>
      <div id="addAllBar" style="display:none;align-items:center;gap:10px">
        <button id="btnAddAll" class="btn-add-all">+ Add All</button>
        <button id="btnConnect" class="btn-connect" disabled style="opacity:0.45">Connect accounts →</button>
      </div>
    </div>
    <p class="already-note" id="alreadyNote" style="display:none"></p>
    <div id="resultsBody"></div>
  </div>
</div>

<script>
var _selectedProvider = null;
var _alreadyConnected = {already_connected_json};

function selectProvider(p) {
  _selectedProvider = p;
  document.querySelectorAll('.provider-card').forEach(function(c){ c.classList.remove('active'); });
  var card = document.getElementById('card-' + p);
  if (card) card.classList.add('active');
  document.getElementById('imapForm').classList.toggle('visible', p === 'imap');
  if (p === 'gmail') startGmailOAuth();
  if (p === 'outlook') startOutlookOAuth();
}

function onImapProviderChange() {
  var v = document.getElementById('imapProvider').value;
  document.getElementById('imapHostField').style.display = v === 'custom' ? 'block' : 'none';
}

function startGmailOAuth() {
  window.location.href = '/email/gmail/auth';
}
function startOutlookOAuth() {
  window.location.href = '/email/outlook/auth';
}

function runImapScan() {
  var btn = document.getElementById('imapScanBtn');
  var status = document.getElementById('imapStatus');
  btn.disabled = true;
  status.style.display = 'inline';
  var provider = document.getElementById('imapProvider').value;
  var host = document.getElementById('imapHost').value.trim();
  var user = document.getElementById('imapUser').value.trim();
  var pass = document.getElementById('imapPass').value;
  fetch('/api/email/scan/imap', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({provider: provider, host: host, username: user, password: pass})
  }).then(function(r){ return r.json(); }).then(function(data){
    btn.disabled = false; status.style.display = 'none';
    if (data.error) { alert('Scan failed: ' + data.error); return; }
    renderResults(data.suggestions, data.already_connected_count);
  }).catch(function(e){
    btn.disabled = false; status.style.display = 'none';
    alert('Connection error: ' + e.message);
  });
}

function renderResults(suggestions, alreadyCount) {
  var sec = document.getElementById('resultsSection');
  var body = document.getElementById('resultsBody');
  sec.style.display = 'block';
  window._allSuggestions = suggestions;
  window._addedSiteKeys = new Set();

  document.getElementById('resultsHeader').textContent =
    suggestions.length > 0 ? 'Found ' + suggestions.length + ' accounts' : 'No new accounts found';
  document.getElementById('resultsSub').textContent =
    suggestions.length > 0
      ? 'These services sent you email. Click Add to connect them to Mighty.'
      : 'All accounts we detected are already connected, or no matching emails were found.';

  // Add All + Connect bar
  var addAllBar = document.getElementById('addAllBar');
  if (suggestions.length > 0) {
    addAllBar.style.display = 'flex';
    document.getElementById('btnAddAll').onclick = addAll;
    document.getElementById('btnConnect').onclick = connectAdded;
  }

  if (alreadyCount > 0) {
    var note = document.getElementById('alreadyNote');
    note.style.display = 'block';
    note.textContent = alreadyCount + ' account' + (alreadyCount !== 1 ? 's' : '') + ' already connected — not shown.';
  }
  if (suggestions.length === 0) {
    body.innerHTML = '<div class="empty-state">✓ Nothing new to add</div>';
    return;
  }
  // Group by category
  var cats = {};
  suggestions.forEach(function(s){
    if (!cats[s.category]) cats[s.category] = [];
    cats[s.category].push(s);
  });
  var catOrder = ['airline','hotel','credit_card','bank','investment','payment',
                  'telecom','streaming','car_rental','retail','dining','gas','health','insurance','tech'];
  var html = '';
  catOrder.forEach(function(cat){
    if (!cats[cat]) return;
    html += '<div class="category-block">';
    html += '<div class="cat-label">' + (window._catLabels[cat] || cat) + '</div>';
    html += '<div class="suggestion-grid">';
    cats[cat].forEach(function(s){
      html += '<div class="sugg-card" id="sc-' + s.site_key + '">';
      html += '<div style="flex:1">';
      html += '<div class="sugg-name">' + escHtml(s.display_name) + '</div>';
      html += '<div class="sugg-count">' + s.email_count + ' email' + (s.email_count !== 1 ? 's' : '') + ' found</div>';
      html += '</div>';
      html += '<div class="sugg-actions">';
      html += '<button class="btn-add" id="btn-add-' + s.site_key + '" onclick="addSuggestion(\'' + s.site_key + '\',\'' + escHtml(s.display_name) + '\')">+ Add</button>';
      html += '<button class="btn-dismiss" onclick="dismissSuggestion(\'' + s.site_key + '\')" title="Dismiss">✕</button>';
      html += '</div></div>';
    });
    html += '</div></div>';
  });
  body.innerHTML = html;
}

function _markAdded(siteKey) {
  window._addedSiteKeys.add(siteKey);
  var card = document.getElementById('sc-' + siteKey);
  var btn  = document.getElementById('btn-add-' + siteKey);
  if (card) card.classList.add('added');
  if (btn)  { btn.textContent = '✓ Added'; btn.disabled = true; }
  updateConnectBar();
}

function updateConnectBar() {
  var n = window._addedSiteKeys ? window._addedSiteKeys.size : 0;
  var connectBtn = document.getElementById('btnConnect');
  if (!connectBtn) return;
  if (n > 0) {
    connectBtn.textContent = 'Connect ' + n + ' account' + (n !== 1 ? 's' : '') + ' →';
    connectBtn.disabled = false;
    connectBtn.style.opacity = '1';
  } else {
    connectBtn.textContent = 'Connect accounts →';
    connectBtn.disabled = true;
    connectBtn.style.opacity = '0.45';
  }
}

function addAll() {
  if (!window._allSuggestions) return;
  var pending = window._allSuggestions.filter(function(s){ return !window._addedSiteKeys.has(s.site_key); });
  if (pending.length === 0) return;
  // Fire all add requests in parallel, then mark cards
  var fetches = pending.map(function(s){
    return fetch('/api/email/suggestions/add', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({site_key: s.site_key})
    }).then(function(){ _markAdded(s.site_key); });
  });
  var btn = document.getElementById('btnAddAll');
  btn.disabled = true;
  btn.textContent = 'Adding…';
  Promise.all(fetches).then(function(){
    btn.textContent = '✓ All added';
    updateConnectBar();
  });
}

function connectAdded() {
  // Go to dashboard; user can set up credentials from there
  window.location.href = '/dashboard';
}

window._catLabels = {already_cat_labels_json};

function escHtml(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

function addSuggestion(siteKey, displayName) {
  fetch('/api/email/suggestions/add', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({site_key: siteKey})
  }).then(function(){ _markAdded(siteKey); });
}

function dismissSuggestion(siteKey) {
  fetch('/api/email/suggestions/dismiss', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({site_key: siteKey})
  });
  var card = document.getElementById('sc-' + siteKey);
  if (card) { card.style.opacity='0'; card.style.transition='opacity .2s'; setTimeout(function(){ card.remove(); },200); }
}

// Auto-render if suggestions were injected server-side (after OAuth callback)
var _injectedSuggestions = {injected_suggestions_json};
var _injectedAlreadyCount = {injected_already_count};
if (_injectedSuggestions !== null) {
  renderResults(_injectedSuggestions, _injectedAlreadyCount);
}

// Show config note if Gmail OAuth not configured
if ({gmail_not_configured}) {
  document.getElementById('noteConfigure').classList.add('visible');
}
</script>
</body>
</html>"""


def _render_email_scan_page(suggestions=None, already_count=0, provider_triggered=None):
    """Render the /email-scan page, optionally with pre-populated scan results."""
    db  = get_db()
    uid = session["user_id"]

    # Already-connected site keys
    acts = db.execute("SELECT source FROM account_credentials WHERE user_id=?", (uid,)).fetchall()
    connected = {r["source"] for r in acts if not r["source"].startswith("_")}

    gmail_configured = bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)
    outlook_configured = bool(MS_CLIENT_ID and MS_CLIENT_SECRET)

    gmail_card = (
        '<div class="provider-card" id="card-gmail" onclick="selectProvider(\'gmail\')">'
        '<div class="provider-icon">📧</div>'
        '<div class="provider-name">Gmail</div>'
        '<div class="provider-desc">Connect with Google — fast, no password needed</div>'
        '</div>'
        if gmail_configured else
        '<div class="provider-card" id="card-gmail" style="opacity:.45;cursor:default" title="Not configured on this server">'
        '<div class="provider-icon">📧</div>'
        '<div class="provider-name">Gmail</div>'
        '<div class="provider-desc">OAuth not configured (use IMAP below)</div>'
        '</div>'
    )
    outlook_card = (
        '<div class="provider-card" id="card-outlook" onclick="selectProvider(\'outlook\')">'
        '<div class="provider-icon">📨</div>'
        '<div class="provider-name">Outlook</div>'
        '<div class="provider-desc">Connect with Microsoft — Hotmail, Live, Office 365</div>'
        '</div>'
        if outlook_configured else
        '<div class="provider-card" id="card-outlook" style="opacity:.45;cursor:default" title="Not configured on this server">'
        '<div class="provider-icon">📨</div>'
        '<div class="provider-name">Outlook</div>'
        '<div class="provider-desc">OAuth not configured (use IMAP below)</div>'
        '</div>'
    )

    import json as _json
    page = _EMAIL_SCAN_PAGE
    page = page.replace("{gmail_card}", gmail_card)
    page = page.replace("{outlook_card}", outlook_card)
    page = page.replace("{already_connected_json}", _json.dumps(list(connected)))
    page = page.replace("{already_cat_labels_json}", _json.dumps(CATEGORY_LABELS))
    page = page.replace("{injected_suggestions_json}", _json.dumps(suggestions))
    page = page.replace("{injected_already_count}", str(already_count))
    page = page.replace("{gmail_not_configured}", "true" if not gmail_configured else "false")
    return page


def _store_suggestions(uid: str, suggestions: list, db):
    """Upsert scan results into email_suggestions table."""
    now = datetime.now(timezone.utc).isoformat()
    for s in suggestions:
        try:
            db.execute("""
                INSERT INTO email_suggestions(user_id,site_key,display_name,category,email_count,sender_domain,created_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(user_id,site_key) DO UPDATE SET
                  email_count=excluded.email_count, dismissed=0, added=0
            """, (uid, s["site_key"], s["display_name"], s["category"],
                  s["email_count"], s.get("sender",""), now))
        except Exception:
            pass
    db.commit()


@app.route("/email-scan")
@require_login
def email_scan_page():
    return _render_email_scan_page()


@app.route("/email/gmail/auth")
@require_login
def email_gmail_auth():
    if not GOOGLE_CLIENT_ID:
        return redirect("/email-scan")
    import urllib.parse
    state = secrets.token_urlsafe(16)
    session["gmail_oauth_state"] = state
    params = {
        "client_id":     GOOGLE_CLIENT_ID,
        "redirect_uri":  base_url() + "/email/gmail/callback",
        "response_type": "code",
        "scope":         "https://www.googleapis.com/auth/gmail.readonly",
        "access_type":   "offline",
        "prompt":        "consent",
        "state":         state,
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return redirect(url)


@app.route("/email/gmail/callback")
@require_login
def email_gmail_callback():
    import urllib.parse
    error = request.args.get("error")
    if error:
        return redirect("/email-scan")

    code  = request.args.get("code", "")
    state = request.args.get("state", "")
    if state != session.pop("gmail_oauth_state", None):
        return "Invalid state", 400

    # Exchange code for tokens
    token_data = {
        "code":          code,
        "client_id":     GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri":  base_url() + "/email/gmail/callback",
        "grant_type":    "authorization_code",
    }
    import urllib.request as _ur
    try:
        req = _ur.Request(
            "https://oauth2.googleapis.com/token",
            data=urllib.parse.urlencode(token_data).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=15) as resp:
            token_resp = json.loads(resp.read())
    except Exception as e:
        return f"Token exchange failed: {e}", 500

    access_token = token_resp.get("access_token", "")
    if not access_token:
        return "No access token received", 500

    # Get the user's email address
    try:
        profile_req = _ur.Request(
            "https://www.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with _ur.urlopen(profile_req, timeout=10) as resp:
            profile = json.loads(resp.read())
        email_address = profile.get("emailAddress", "")
    except Exception:
        email_address = ""

    db  = get_db()
    uid = session["user_id"]

    # Store connection (encrypt tokens)
    enc_access  = encrypt_cred(uid, access_token)
    enc_refresh = encrypt_cred(uid, token_resp.get("refresh_token", "")) if token_resp.get("refresh_token") else ""
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO email_connections(user_id,provider,access_token_enc,refresh_token_enc,email_address,scanned_at,created_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(user_id,provider) DO UPDATE SET
          access_token_enc=excluded.access_token_enc,
          refresh_token_enc=excluded.refresh_token_enc,
          email_address=excluded.email_address,
          scanned_at=excluded.scanned_at
    """, (uid, "gmail", enc_access, enc_refresh, email_address, now, now))
    db.commit()

    # Run the scan
    acts = db.execute("SELECT source FROM account_credentials WHERE user_id=?", (uid,)).fetchall()
    connected = {r["source"] for r in acts if not r["source"].startswith("_")}
    try:
        suggestions = scan_gmail(access_token, already_connected=connected)
    except Exception as e:
        suggestions = []

    already_count = sum(1 for s in suggestions if s["site_key"] in connected)
    visible = [s for s in suggestions if s["site_key"] not in connected]
    _store_suggestions(uid, visible, db)

    return _render_email_scan_page(suggestions=visible, already_count=already_count)


@app.route("/email/outlook/auth")
@require_login
def email_outlook_auth():
    if not MS_CLIENT_ID:
        return redirect("/email-scan")
    import urllib.parse
    state = secrets.token_urlsafe(16)
    session["outlook_oauth_state"] = state
    params = {
        "client_id":     MS_CLIENT_ID,
        "response_type": "code",
        "redirect_uri":  base_url() + "/email/outlook/callback",
        "scope":         "Mail.Read offline_access",
        "state":         state,
    }
    url = "https://login.microsoftonline.com/common/oauth2/v2.0/authorize?" + urllib.parse.urlencode(params)
    return redirect(url)


@app.route("/email/outlook/callback")
@require_login
def email_outlook_callback():
    import urllib.parse
    error = request.args.get("error")
    if error:
        return redirect("/email-scan")

    code  = request.args.get("code", "")
    state = request.args.get("state", "")
    if state != session.pop("outlook_oauth_state", None):
        return "Invalid state", 400

    token_data = {
        "client_id":     MS_CLIENT_ID,
        "client_secret": MS_CLIENT_SECRET,
        "code":          code,
        "redirect_uri":  base_url() + "/email/outlook/callback",
        "grant_type":    "authorization_code",
        "scope":         "Mail.Read offline_access",
    }
    import urllib.request as _ur
    try:
        req = _ur.Request(
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            data=urllib.parse.urlencode(token_data).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=15) as resp:
            token_resp = json.loads(resp.read())
    except Exception as e:
        return f"Token exchange failed: {e}", 500

    access_token = token_resp.get("access_token", "")
    if not access_token:
        return "No access token received", 500

    db  = get_db()
    uid = session["user_id"]

    enc_access  = encrypt_cred(uid, access_token)
    enc_refresh = encrypt_cred(uid, token_resp.get("refresh_token", "")) if token_resp.get("refresh_token") else ""
    now = datetime.now(timezone.utc).isoformat()
    db.execute("""
        INSERT INTO email_connections(user_id,provider,access_token_enc,refresh_token_enc,email_address,scanned_at,created_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(user_id,provider) DO UPDATE SET
          access_token_enc=excluded.access_token_enc,
          refresh_token_enc=excluded.refresh_token_enc,
          scanned_at=excluded.scanned_at
    """, (uid, "outlook", enc_access, enc_refresh, "", now, now))
    db.commit()

    acts = db.execute("SELECT source FROM account_credentials WHERE user_id=?", (uid,)).fetchall()
    connected = {r["source"] for r in acts if not r["source"].startswith("_")}
    try:
        suggestions = scan_outlook(access_token, already_connected=connected)
    except Exception:
        suggestions = []

    already_count = sum(1 for s in suggestions if s["site_key"] in connected)
    visible = [s for s in suggestions if s["site_key"] not in connected]
    _store_suggestions(uid, visible, db)

    return _render_email_scan_page(suggestions=visible, already_count=already_count)


@app.route("/api/email/scan/imap", methods=["POST"])
@require_login
def api_email_scan_imap():
    data     = request.get_json(force=True) or {}
    provider = data.get("provider", "custom")
    username = data.get("username", "").strip()
    password = data.get("password", "")
    host     = data.get("host", "").strip()

    preset = get_imap_preset(provider)
    if not host:
        host = preset["host"]
    port   = int(data.get("port", preset.get("port", 993)))
    use_ssl = preset.get("ssl", True)

    if not host or not username or not password:
        return jsonify({"error": "host, username, and password are required"}), 400

    db  = get_db()
    uid = session["user_id"]
    acts = db.execute("SELECT source FROM account_credentials WHERE user_id=?", (uid,)).fetchall()
    connected = {r["source"] for r in acts if not r["source"].startswith("_")}

    try:
        suggestions = scan_imap(host, port, username, password,
                                already_connected=connected, use_ssl=use_ssl)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    already_count = 0  # scan_imap already filters
    visible = suggestions
    _store_suggestions(uid, visible, db)

    return jsonify({
        "suggestions": visible,
        "already_connected_count": already_count,
        "total": len(visible),
    })


@app.route("/api/email/suggestions")
@require_login
def api_email_suggestions():
    db  = get_db()
    uid = session["user_id"]
    rows = db.execute("""
        SELECT site_key, display_name, category, email_count, sender_domain
        FROM email_suggestions
        WHERE user_id=? AND dismissed=0 AND added=0
        ORDER BY email_count DESC
    """, (uid,)).fetchall()
    return jsonify({"suggestions": [dict(r) for r in rows]})


@app.route("/api/email/suggestions/dismiss", methods=["POST"])
@require_login
def api_email_suggestions_dismiss():
    data = request.get_json(force=True) or {}
    site_key = data.get("site_key", "")
    db  = get_db()
    uid = session["user_id"]
    db.execute("UPDATE email_suggestions SET dismissed=1 WHERE user_id=? AND site_key=?", (uid, site_key))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/email/suggestions/add", methods=["POST"])
@require_login
def api_email_suggestions_add():
    data = request.get_json(force=True) or {}
    site_key = data.get("site_key", "")
    db  = get_db()
    uid = session["user_id"]
    db.execute("UPDATE email_suggestions SET added=1 WHERE user_id=? AND site_key=?", (uid, site_key))
    db.commit()
    return jsonify({"ok": True})


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Start cloud sync scheduler if running on Railway
    if os.environ.get("ENABLE_CLOUD_SYNC", "").lower() == "true":
        _start_cloud_scheduler()
    # Start expiry alert scheduler (opt-out via ENABLE_EXPIRY_ALERTS=false)
    if os.environ.get("ENABLE_EXPIRY_ALERTS", "true").lower() == "true":
        _start_alert_scheduler()
    app.run(host="0.0.0.0", port=PORT, debug=False)
