"""
Mighty Self-Serve
=================
Personal authorization layer for AI agents.
Self-contained Flask app — SQLite, no external dependencies.

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
        _rl_store[key] = ts
        return True

DATABASE        = os.environ.get("DATABASE_PATH", "/app/data/mighty.db")
PORT            = int(os.environ.get("PORT", 5004))
TIMEOUT_SEC     = 300  # pending authorization expires after 5 minutes
POSTMARK_API_KEY = os.environ.get("POSTMARK_API_KEY", "")
POSTMARK_FROM    = os.environ.get("POSTMARK_FROM", "Mighty <noreply@mighty.ai>")
NOTIFY_EMAIL_OVERRIDE = os.environ.get("NOTIFY_EMAIL", "")  # override recipient for sandbox testing
if NOTIFY_EMAIL_OVERRIDE:
    print(f"[Mighty] WARNING: NOTIFY_EMAIL_OVERRIDE is set — all notification emails go to {NOTIFY_EMAIL_OVERRIDE}", flush=True)
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

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
                UNIQUE(site, path)
            );
        """)
        # Pre-seed with known-good paths (quality_score=5 → treated as trusted immediately)
        _KNOWN_PATHS = [
            ('delta',      '/my-profile/certificates'),
            ('delta',      '/us/en/my-account/eCredits'),
            ('delta',      '/myprofile'),
            ('marriott',   '/loyalty/myAccount/certificates'),
            ('marriott',   '/loyalty/myAccount/benefits'),
            ('hilton',     '/en/hilton-honors/profile/awards'),
            ('hilton',     '/en/hilton-honors/profile/benefits'),
            ('hyatt',      '/en-US/my-account/awards'),
            ('united',     '/en/us/myaccount/awards'),
            ('alaska_air', '/account/wallet'),
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

init_db()
print(f"[Mighty] POSTMARK_API_KEY={'set' if POSTMARK_API_KEY else 'NOT SET'}", flush=True)


# ── VAPID key management ──────────────────────────────────────────────────────

def get_vapid_keys():
    """Return (private_key_base64url, public_key_base64url) — generating once and caching in DB."""
    import base64
    from py_vapid import Vapid
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

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

VAPID_PRIVATE, VAPID_PUBLIC = get_vapid_keys()
print(f"[Mighty] VAPID public key: {VAPID_PUBLIC[:20]}...", flush=True)


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
        if secs < 60:   return "just now"
        mins = secs // 60
        if mins < 60:   return f"{mins} minute{'s' if mins != 1 else ''} ago"
        hrs = mins // 60
        if hrs < 24:    return f"{hrs} hour{'s' if hrs != 1 else ''} ago"
        days = hrs // 24
        return f"{days} day{'s' if days != 1 else ''} ago"
    except Exception:
        return ts[:10] if ts else "—"

def _sidebar_html(active: str, email: str, csrf: str) -> str:
    """Generate the shared left sidebar HTML — icon-only, 48px."""
    def _nav(href, label, icon_svg, page_key):
        cls = "sidebar-link sidebar-link-active" if active == page_key else "sidebar-link"
        return f'<a href="{href}" class="{cls}">{icon_svg}<span class="sidebar-tip">{label}</span></a>'
    icon_dash = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/></svg>'
    icon_acct = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M10 13a5 5 0 007.54.54l3-3a5 5 0 00-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 00-7.54-.54l-3 3a5 5 0 007.07 7.07l1.71-1.71"/></svg>'
    icon_sett = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>'
    av = (email[0] if email else "?").upper()
    return (
        '<aside class="sidebar">'
        '<div class="sidebar-header">'
        '<a href="/dashboard" class="sidebar-logo">'
        '<img src="/logo-icon.png" alt="Mighty" class="sidebar-logo-img">'
        '<span class="sidebar-tip">Mighty</span>'
        '</a></div>'
        '<nav class="sidebar-nav">'
        + _nav('/dashboard', 'Dashboard', icon_dash, 'dashboard')
        + _nav('/credentials', 'Accounts', icon_acct, 'accounts')
        + _nav('/settings', 'Settings', icon_sett, 'settings')
        + '</nav>'
        '<div class="sidebar-footer">'
        f'<form method="POST" action="/logout" style="margin:0;display:flex;justify-content:center">'
        f'<input type="hidden" name="_csrf" value="{he(csrf)}">'
        f'<button class="sidebar-avatar" type="submit">{av}<span class="sidebar-tip">Sign out</span></button>'
        '</form>'
        '</div>'
        '</aside>'
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

DISCOVER_PROMPT = """You are analyzing one or more pages from a user's {site} account.
Pages may be separated by === URL === markers.
Today's date: {today}.

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
- "Gold Medallion" status → INCLUDE as {{"key":"elite_status","label":"Elite Status","value":"Gold Medallion"}}
- "24,617 Rapid Rewards points" → INCLUDE as {{"key":"rapid_rewards_points","label":"Rapid Rewards Points","value":"24,617"}}
- "0 of 20 flights" in A-List section → INCLUDE as {{"key":"alist_progress","label":"A-List Flights Progress","value":"0 of 20"}}
- "$2,472.20 Total Payment Due" → INCLUDE as {{"key":"balance_due","label":"Balance Due","value":"$2,472.20"}}
- "Minimum Payment Due: $35 by Jul 12, 2026" → INCLUDE as {{"key":"min_payment_due","label":"Minimum Payment Due","value":"$35 by Jul 12, 2026"}}
- "Past Due Amount: $150" → INCLUDE as {{"key":"past_due_amount","label":"Past Due Amount","value":"$150"}}
- "Last payment: $2,472.20 received Jun 11, 2026" → INCLUDE as {{"key":"last_payment","label":"Last Payment Received","value":"$2,472.20 on Jun 11, 2026"}}
- "AutoPay: Enrolled" → INCLUDE as {{"key":"autopay_status","label":"Auto Pay Status","value":"Enrolled"}}
- "Free Night Award — expires Dec 31, 2026" → INCLUDE as {{"key":"free_night_award","label":"Free Night Award Expiry","value":"Dec 31, 2026"}}
- "2 Suite Night Awards available" → INCLUDE as {{"key":"suite_night_awards","label":"Suite Night Awards","value":"2 available"}}
- "Annual travel credit: $187 remaining" → INCLUDE as {{"key":"travel_credit_remaining","label":"Travel Credit Remaining","value":"$187"}}
- "Earn 5,000 bonus miles — book by Jul 15" → INCLUDE as {{"key":"bonus_miles_offer","label":"Bonus Miles Offer Deadline","value":"Jul 15, 2026"}}
- "Global Upgrade Certificate — 1 available, expires Dec 31, 2026" → INCLUDE as {{"key":"upgrade_certificates","label":"Global Upgrade Certificates","value":"1 (exp Dec 31, 2026)"}}
- "Companion Certificate — valid through Jan 15, 2027" → INCLUDE as {{"key":"companion_certificate","label":"Companion Certificate","value":"Valid through Jan 15, 2027"}}
- "Regional Upgrade Certificates: 4 available" → INCLUDE as {{"key":"regional_upgrade_certs","label":"Regional Upgrade Certificates","value":"4 available"}}
- "Priority Pass membership — unlimited lounge visits" → INCLUDE as {{"key":"priority_pass","label":"Priority Pass Lounge Access","value":"Unlimited visits"}}
- "Free checked bag on all Delta flights" → INCLUDE as {{"key":"free_checked_bag","label":"Free Checked Bag Benefit","value":"All Delta flights"}}
- "Upcoming flight: SFO → JFK, Aug 14, 2026" → INCLUDE as {{"key":"upcoming_flight","label":"Upcoming Flight","value":"SFO → JFK, Aug 14, 2026"}}

LABELING: write labels that make sense without knowing the site (no abbreviations, no page jargon). Labels should say what the value IS, not repeat the site name.

Return ONLY a JSON array, no other text:
[{{"key":"rapid_rewards_points","label":"Rapid Rewards Points","value":"24,617"}}]

Rules:
- key: snake_case, 1-4 words
- label: 2-5 words, self-explanatory out of context
- value: exact current value — if empty, zero, or a login prompt, skip the field entirely
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

def _post_filter_fields(fields: list) -> list:
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
    )

    # Patterns for values that are pure long numeric IDs (>12 consecutive digits)
    _LONG_NUM_RE = _re.compile(r'^\d{12,}$')

    def _is_past_date(value: str) -> bool:
        """Return True if value is a date string that is in the past."""
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%b %d, %Y", "%B %d, %Y",
                    "%d %b %Y", "%d %B %Y", "%Y/%m/%d"):
            try:
                d = _dt.datetime.strptime(value.strip(), fmt).date()
                return d < _today
            except ValueError:
                pass
        return False

    out = []
    for f in fields:
        label = (f.get("label") or "").strip()
        value = str(f.get("value") or "").strip()
        lbl_low = label.lower()

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

        # Drop upcoming-flight/reservation fields where the embedded date is past
        _date_match = _re.search(
            r'\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b', value + " " + label
        )
        if _date_match and _is_past_date(_date_match.group(0)):
            # Only drop if the label suggests it's a booking/reservation/flight
            _BOOKING_TERMS = ("flight", "reservation", "booking", "trip",
                              "check-in", "check-out", "arrival", "departure",
                              "stay", "itinerary", "travel")
            if any(t in lbl_low for t in _BOOKING_TERMS):
                continue

        out.append(f)
    return out


def claude_discover_fields(raw_text: str, site_name: str) -> list:
    """Use Gemini Flash to identify all useful data fields in a page."""
    if not _claude or not raw_text:
        return []
    try:
        print(f"[Mighty] Discovering fields for {site_name} ({len(raw_text)} chars). Preview: {raw_text[:500]!r}", flush=True)
        from datetime import datetime as _dtm
        _today_str = _dtm.utcnow().strftime("%B %d, %Y")
        prompt = DISCOVER_PROMPT.format(site=site_name, text=raw_text[:10000], today=_today_str)
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
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return _post_filter_fields(result)
            # Handle {"fields": [...]} or similar wrapper
            if isinstance(result, dict):
                for k in ("fields", "data", "items", "results"):
                    if isinstance(result.get(k), list):
                        return _post_filter_fields(result[k])
            return []
        except json.JSONDecodeError:
            m = re.search(r'\[.*\]', text, re.DOTALL)
            if m:
                try: return _post_filter_fields(json.loads(m.group()))
                except Exception: pass
            return []
    except Exception as e:
        print(f"[Mighty] Gemini discovery error: {e}", flush=True)
        return []

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
    key  = data.get("api_key") or request.headers.get("X-Mighty-Key", "")
    if not key:
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
.main-content{flex:1;min-width:0;height:100vh;overflow-y:auto;display:flex;flex-direction:column}
/* Top bar */
.topbar{padding:14px 24px;display:flex;align-items:center;gap:10px;flex-shrink:0;background:#eee9e2;position:sticky;top:0;z-index:2;border-bottom:0.5px solid rgba(0,0,0,0.07)}
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
/* Page body */
.page-body{flex:1;padding:20px 24px 32px;box-sizing:border-box}
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
.acct-card.is-stale{opacity:0.55}
.acct-card.is-expiring{border-color:rgba(217,119,6,0.35) !important;box-shadow:0 1px 1px rgba(0,0,0,0.03),0 3px 12px rgba(217,119,6,0.1) !important}
.acct-card.highlight-off{opacity:0.28;filter:grayscale(0.3)}
.acct-card.highlight-on{box-shadow:0 4px 20px rgba(0,0,0,0.12),0 0 0 2.5px rgba(37,99,235,0.7) !important;transform:translateY(-2px);background:#fff !important}
/* Card footer */
.acct-footer{display:flex;align-items:center;justify-content:space-between;padding:8px 14px;border-top:0.5px solid rgba(0,0,0,0.05);gap:8px}
.acct-expand-btn{font-size:11px;font-weight:500;color:#9ca3af;background:none;border:none;cursor:pointer;padding:0;font-family:inherit;display:flex;align-items:center;gap:4px;transition:color 0.1s}
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
@media(max-width:768px){html,body{height:auto;overflow:auto}.sidebar{display:none}.main-content{height:auto;overflow:visible}}
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
    <div class="topbar-search">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <input type="text" placeholder="Search accounts…" oninput="filterCards(this.value)" id="card-search">
    </div>
    <div style="flex:1"></div>
    {agent_status_indicator}
    <div id="pending-badge" style="display:{pending_display}" class="pending-pill">
      {pending_count} awaiting decision
    </div>
    <button id="rediscover-btn" onclick="rediscoverAll()" class="btn-sync" title="Re-extract fields from existing account data">
      <svg id="rediscover-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <span id="rediscover-label">Re-discover</span>
    </button>
    <button id="cloud-sync-btn" onclick="cloudSync()" class="btn-sync" title="Refresh all account data">
      <svg id="sync-icon" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0"><path d="M21 2v6h-6"/><path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M3 22v-6h6"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/></svg>
      <span id="sync-label">Sync All</span>
    </button>
  </div>

  <div id="mighty-toast"></div>

  <div class="page-body" {feed_col_hidden}>
    <input type="hidden" name="_csrf" value="{csrf_token}">

    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:18px;gap:12px">
      <div class="feed-tabs">
        <button class="feed-tab active" id="ftab-accounts" onclick="switchFeedTab('accounts',this)">Account Data</button>
        <button class="feed-tab" id="ftab-activity" onclick="switchFeedTab('activity',this)">Activity Log</button>
      </div>
      <div style="display:flex;align-items:center;gap:8px">
        {agent_cta_button}
        <a href="/credentials" class="btn-connect">+ Connect account</a>
      </div>
    </div>

    <div id="expiring-banner" style="display:{expiring_display};align-items:center;gap:10px;background:#fffbeb;border:0.5px solid rgba(217,119,6,0.3);border-radius:10px;padding:10px 16px;margin-bottom:16px;cursor:pointer" data-base-display="{expiring_display}" onclick="toggleExpiringFilter(this)">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#d97706" stroke-width="2" style="flex-shrink:0"><circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/></svg>
      <span style="font-size:13px;font-weight:600;color:#92400e"><span id="expiring-count">{expiring_count}</span> account{expiring_plural} with expiring benefits or upcoming due dates</span>
      <span style="font-size:11px;color:#b45309;margin-left:auto" id="expiring-filter-label">Click to highlight</span>
    </div>

    <div id="fview-accounts">
      {account_data_html}
    </div>

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
    </div>
  </div>
</div>


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
// Restore scroll position after any reload — defer until paint to avoid layout-shift reset
(function() {
  var fc = document.querySelector('.feed-col');
  var saved = sessionStorage.getItem('mighty-feed-scroll');
  if (fc && saved) { fc.scrollTop = parseInt(saved); sessionStorage.removeItem('mighty-feed-scroll'); }
  var sy = sessionStorage.getItem('mighty-scroll-y');
  if (sy) {
    sessionStorage.removeItem('mighty-scroll-y');
    // double-rAF ensures we run after the browser has committed the first layout
    // NOTE: scroll lives on .main-content (overflow-y:auto), not window
    requestAnimationFrame(function() {
      requestAnimationFrame(function() {
        var mc = document.querySelector('.main-content');
        if (mc) { mc.scrollTop = parseInt(sy); } else { window.scrollTo(0, parseInt(sy)); }
      });
    });
  }
})();

function reloadWithScroll() {
  var fc = document.querySelector('.feed-col');
  if (fc) sessionStorage.setItem('mighty-feed-scroll', fc.scrollTop);
  // Scroll lives on .main-content, not window
  var mc = document.querySelector('.main-content');
  sessionStorage.setItem('mighty-scroll-y', mc ? mc.scrollTop : (window.scrollY || document.documentElement.scrollTop || 0));
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

// Auto-reload if any account is still discovering fields (max 4 attempts)
if (document.querySelector('[data-discovering="1"]')) {
  var _discoverReloads = parseInt(localStorage.getItem('mighty-discover-reloads') || '0');
  if (_discoverReloads < 4) {
    localStorage.setItem('mighty-discover-reloads', _discoverReloads + 1);
    setTimeout(reloadWithScroll, 12000);
  } else {
    // Retries exhausted — keep counter at 99 so we don't restart the cycle on next page load
    // (counter resets to 0 naturally when fields are successfully found and card loses data-discovering)
    localStorage.setItem('mighty-discover-reloads', '99');
    document.querySelectorAll('[data-discovering="1"]').forEach(function(el) {
      el.innerHTML = '<span style="color:#9ca3af;font-size:12px;font-style:italic">No fields found — use ↻ to retry sync</span>';
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
    var rel = fmtRelative(ts);
    if (rel) el.textContent = 'Synced ' + rel;
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
  if (minsAgo > 30) {
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
function _showToast(msg, duration) {
  var t = document.getElementById('mighty-toast');
  if (!t) return;
  t.textContent = msg;
  t.className = 'show';
  setTimeout(function(){ t.className = 'hide'; }, (duration || 3000) - 200);
  setTimeout(function(){ t.className = ''; }, duration || 3000);
}
function rediscoverAll() {
  var btn = document.getElementById('rediscover-btn');
  var lbl = document.getElementById('rediscover-label');
  if (!btn || btn.disabled) return;
  btn.disabled = true;
  btn.classList.add('rediscovering');
  lbl.textContent = 'Re-discovering…';
  fetch('/api/data/rediscover-all', {method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'},
    body:'_csrf=' + encodeURIComponent(document.querySelector('input[name="_csrf"]') ?
      document.querySelector('input[name="_csrf"]').value : '')
  }).then(function(r){return r.json();}).then(function(d){
    if (d.ok) {
      _showToast('Re-discovering fields across ' + d.sources + ' account' + (d.sources !== 1 ? 's' : '') + '…', 4000);
      setTimeout(function(){
        btn.classList.remove('rediscovering');
        lbl.textContent = 'Re-discover';
        btn.disabled = false;
        reloadWithScroll();
      }, 20000);
    } else {
      _showToast('Re-discover failed — try again', 3000);
      btn.classList.remove('rediscovering');
      lbl.textContent = 'Re-discover';
      btn.disabled = false;
    }
  }).catch(function(){
    _showToast('Re-discover failed — try again', 3000);
    btn.classList.remove('rediscovering');
    lbl.textContent = 'Re-discover';
    btn.disabled = false;
  });
}
// Extension presence detection — dashboard_relay.js sends this on load
var _extPresent = false;
window.addEventListener('message', function(e) {
  if (e.data && e.data.type === '__mighty_ext_present__') _extPresent = true;
});

function _finishSync() {
  var btn = document.getElementById('cloud-sync-btn');
  document.querySelectorAll('.acct-card').forEach(function(c){ c.classList.remove('is-syncing'); });
  if (btn) { btn.classList.remove('syncing'); btn.disabled = false; }
  _setSyncLabel('Sync All');
  reloadWithScroll();
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
  fetch('/sync/account/' + source, {
    method: 'POST',
    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
    body: new URLSearchParams({_csrf: csrf})
  }).then(function(r) {
    if (!r.ok) { btn.innerHTML = orig; btn.disabled = false; return; }
    var poll = setInterval(function() {
      fetch('/sync/status').then(function(r2){ return r2.json(); }).then(function(s) {
        if (!s.running) {
          clearInterval(poll);
          reloadWithScroll();
        }
      });
    }, 2000);
  }).catch(function() {
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
  fetch('/dashboard/has-pending').then(function(r) { return r.json(); }).then(function(d) {
    if (d.pending !== lastPending) {
      var fc = document.querySelector('.feed-col');
      if (fc) sessionStorage.setItem('mighty-feed-scroll', fc.scrollTop);
      var mc = document.querySelector('.main-content');
      sessionStorage.setItem('mighty-scroll-y', mc ? mc.scrollTop : (window.scrollY || document.documentElement.scrollTop || 0));
      location.reload();
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
</script>
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
@media(max-width:768px){html,body{height:auto;overflow:auto}.sidebar{display:none}.main-content{height:auto;overflow:visible}}
</style>
</head>
<body>
{_SIDEBAR_}

<div class="main-content">
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
    <div style="display:flex;align-items:center;justify-content:flex-end;margin-top:4px">
      <span id="save-ind" style="font-size:11px;color:#34d399;display:none">Saved ✓</span>
    </div>
  </div>

  <div class="card">
    <div class="section-title">Account</div>
    <div style="font-size:13px;color:#8892a4;margin-bottom:16px">Signed in as <span style="color:#1c1917;font-weight:600">{email}</span></div>
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
      <a href="/onboarding" style="display:inline-block;padding:8px 14px;background:#f5f2ed;color:#6366f1;border:1px solid #e8e4de;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none">↺ Re-run setup</a>
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

@app.route("/dashboard")
@require_login
def dashboard():
    expire_pending()
    db    = get_db()
    user  = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
    if not user:
        session.clear()
        return redirect("/login")
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


    onboarding_banner = ""
    # Only show the banner in the active (non-empty) state — the empty welcome state handles its own CTA
    if not user["onboarded"] and len(acts) > 0:
        onboarding_banner = (
            '<div style="background:rgba(129,140,248,0.08);border:1px solid rgba(129,140,248,0.2);'
            'border-radius:10px;padding:14px 18px;display:flex;align-items:center;'
            'justify-content:space-between;gap:16px;margin:0 32px 8px">'
            '<div style="font-size:13px;color:#4338ca">'
            'Finish setting up Mighty to connect your first agent.</div>'
            '<a href="/onboarding" style="font-size:13px;font-weight:600;color:#6366f1;white-space:nowrap">'
            'Complete setup &#8594;</a></div>'
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
                '<div style="display:flex;align-items:center;gap:6px;font-size:12px;color:#34d399">'
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
    def _fmt_sync(ts):
        try:
            clean = ts.replace('Z', '+00:00') if ts and ts.endswith('Z') else ts
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            secs = int((utcnow() - dt).total_seconds())
            if secs < 60:   return "just now"
            mins = secs // 60
            if mins < 60:   return f"{mins} minute{'s' if mins != 1 else ''} ago"
            hrs = mins // 60
            if hrs < 24:    return f"{hrs} hour{'s' if hrs != 1 else ''} ago"
            days = hrs // 24
            return f"{days} day{'s' if days != 1 else ''} ago"
        except Exception:
            return ts[:10] if ts else "—"

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
                if discovered and enabled:
                    discovered_by_source[cr["source"]] = {"fields": discovered, "enabled": enabled}
                elif ex.get("discovery_failed"):
                    discovered_by_source[cr["source"]] = {"fields": [], "enabled": set(), "failed": True}
            except Exception:
                pass

    acct_rows = get_db().execute(
        "SELECT * FROM account_data WHERE user_id=? ORDER BY synced_at DESC",
        (user["id"],)
    ).fetchall()
    synced_map = {r["source"]: r for r in acct_rows}

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
            if src in discovered_by_source:
                disc  = discovered_by_source[src]
                items = [
                    {"key": f["key"], "label": f["label"], "value": f.get("value", "–")}
                    for f in disc["fields"] if f.get("key") in disc["enabled"]
                ]
            else:
                items = data.get("items", [])

            synced_at   = row["synced_at"] if row else ""
            sync_status = data.get("sync_status", "ok") if row else ""
            status_color = "#30d158"

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

            # Build hero section
            if hero_item:
                hero_html = (
                    f'<div class="acct-divider"></div>'
                    f'<div class="acct-hero">'
                    f'<div class="hero-val" title="{he(hero_item["value"])}">{he(hero_item["value"])}</div>'
                    f'<div class="hero-lbl">{he(hero_item["label"])}</div>'
                    f'</div>'
                )
            elif sync_status == "login_required":
                hero_html = (
                    f'<div class="acct-divider"></div>'
                    f'<div class="acct-hero">'
                    f'<div style="color:#ef4444;font-size:12px;font-weight:500">⚠ Login required — sync to reconnect</div>'
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
                    hero_html = (
                        f'<div class="acct-divider"></div>'
                        f'<div class="acct-hero">'
                        f'<div style="color:#d97706;font-size:12px">No account data — sync to retry</div>'
                        f'</div>'
                    )
                    status_color = "#f59e0b"
                else:
                    hero_html = (
                        f'<div class="acct-divider"></div>'
                        f'<div class="acct-hero" data-discovering="1">'
                        f'<div style="color:#6366f1;font-size:12px;font-weight:500">'
                        f'<span style="display:inline-block;animation:spin 1.2s linear infinite;margin-right:4px">↻</span>'
                        f'Discovering fields…</div>'
                        f'</div>'
                    )
                    status_color = "#9ca3af"
            else:
                hero_html = (
                    f'<div class="acct-divider"></div>'
                    f'<div class="acct-hero">'
                    f'<div style="color:#c0bab4;font-style:italic;font-size:12px">Awaiting sync…</div>'
                    f'</div>'
                )
                status_color = "#9ca3af"

            # Build secondary stats (up to 2 visible)
            sec_html = ""
            if secondary_items:
                rows = "".join(
                    f'<div class="sec-row">'
                    f'<span class="sec-lbl">{he(i["label"])}</span>'
                    f'<span class="sec-val" title="{he(i["value"])}">{he(i["value"])}</span>'
                    f'</div>'
                    for i in secondary_items[:2]
                )
                sec_html = f'<div class="acct-secondary">{rows}</div>'

            # Build expanded fields (all items not already shown)
            shown_keys = set()
            if hero_item:
                shown_keys.add(hero_item.get("key"))
            if alert_item:
                shown_keys.add(alert_item.get("key"))
            for i in secondary_items[:2]:
                shown_keys.add(i.get("key"))
            extra_items = [i for i in items if i.get("key") not in shown_keys]
            expanded_html = ""
            if extra_items:
                exp_rows = "".join(
                    f'<div class="exp-row">'
                    f'<span class="exp-lbl" title="{he(i["label"])}">{he(i["label"])}</span>'
                    f'<span class="exp-val" title="{he(i["value"])}">{he(i["value"])}</span>'
                    f'</div>'
                    for i in extra_items
                )
                expanded_html = f'<div class="acct-expanded">{exp_rows}</div>'

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
            synced_title = "Synced recently" if status_color == "#30d158" else "Not yet synced"
            stale_cls = " is-stale" if not synced_at else ""
            expiring_cls = " is-expiring" if alert_item else ""

            # Footer: expand toggle only if there are extra items
            if extra_items:
                expand_btn = (
                    f'<button class="acct-expand-btn" onclick="toggleExpand(this)" '
                    f'data-count="{len(extra_items)}">'
                    f'<svg width="10" height="10" viewBox="0 0 10 10" fill="none"><path d="M2 4l3 3 3-3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>'
                    f'{len(extra_items)} more field{"s" if len(extra_items)!=1 else ""}'
                    f'</button>'
                )
            else:
                expand_btn = '<span style="flex:1"></span>'

            card_footer = (
                f'<div class="acct-footer">'
                f'{expand_btn}'
                f'<a href="/credentials#card-{he(src)}" class="acct-edit-btn">Edit fields</a>'
                f'</div>'
            )

            grid_cards += (
                f'<div class="acct-card{stale_cls}{expiring_cls}" data-name="{he(display_name)}">'
                f'<div class="acct-card-header">'
                f'<div style="flex:1;min-width:0">'
                f'<div class="acct-name">{he(display_name)}</div>'
                f'<div class="acct-sync-time" data-synced="{he(synced_at)}">{sync_label}</div>'
                f'</div>'
                f'<div class="acct-controls">'
                f'<div style="width:7px;height:7px;border-radius:50%;background:{status_color};flex-shrink:0;cursor:help" title="{synced_title}"></div>'
                f'<button onclick="syncAccount(\'{he(src)}\', this)" title="Sync this account" class="acct-refresh-btn">↻</button>'
                f'</div>'
                f'</div>'
                f'{bad_banner}'
                f'{hero_html}'
                f'{sec_html}'
                f'{alert_html}'
                f'{expanded_html}'
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
            .replace("{csrf_token}",              _csrf))

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
    return (SETTINGS_HTML
            .replace("{_SIDEBAR_}",               _sidebar_html('settings', user["email"], _csrf))
            .replace("{email}",                   he(user["email"]))
            .replace("{api_key}",                 raw_key)
            .replace("{api_key_masked}",          he(api_key_masked))
            .replace("{ntfy_topic}",              topic)
            .replace("{push_checked}",            "checked" if user["notify_push"]    else "")
            .replace("{ntfy_checked}",            "checked" if user["notify_ntfy"]    else "")
            .replace("{email_checked}",           "checked" if user["notify_email"]   else "")
            .replace("{minimal_logging_checked}", "checked" if user["minimal_logging"] else "")
            .replace("{postmark_warn}",           postmark_warn)
            .replace("{postmark_js}",             "true" if postmark_ok else "false")
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


@app.route("/settings/privacy", methods=["POST"])
@require_login
def update_privacy():
    check_csrf()
    data = request.get_json(force=True)
    get_db().execute(
        "UPDATE users SET minimal_logging=? WHERE id=?",
        (1 if data.get("minimal_logging") else 0, session["user_id"])
    )
    get_db().commit()
    return jsonify({"ok": True})


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
    data     = request.get_json(force=True)
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
    get_db().execute("DELETE FROM push_subscriptions WHERE user_id=?", (session["user_id"],))
    get_db().commit()
    return jsonify({"ok": True})

@app.route("/api/push/vapid-public-key")
def vapid_public_key():
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
    return jsonify({
        "ok":         True,
        "db_ok":      db_ok,
        "db_path":    DATABASE,
        "user_count": user_count,
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

    existing  = ex.get("discovered_fields", [])
    existing_keys = {f["key"] for f in existing}
    ex_by_key = {f["key"]: f for f in existing}
    ex_enabled = set(ex.get("enabled_fields", []))
    truly_new_keys: list = []
    for f in fields:
        key = f["key"]
        if key in ex_by_key: ex_by_key[key]["value"] = f.get("value", "")
        else:
            ex_by_key[key] = f
            ex_enabled.add(key)
            if key not in existing_keys:
                truly_new_keys.append(key)

    # Re-order: Gemini's latest response ordering takes precedence (fixes hero field)
    gemini_keys = [f["key"] for f in fields]
    reordered = [ex_by_key[k] for k in gemini_keys if k in ex_by_key]
    reordered += [f for k, f in ex_by_key.items() if k not in set(gemini_keys)]

    # Dedup by label similarity
    def _n(s): return re.sub(r'[^a-z0-9]', '', s.lower())
    seen_labels: set = set(); seen_vals: dict = {}; deduped = []
    for f in reordered:
        val = str(f.get("value", "")).strip(); lbl = _n(f.get("label", ""))
        if any(lbl in sl or sl in lbl for sl in seen_labels): ex_enabled.discard(f["key"]); continue
        if val and val not in ("0", "") and val in seen_vals: ex_enabled.discard(f["key"]); continue
        seen_labels.add(lbl)
        if val and val not in ("0", ""): seen_vals[val] = f["key"]
        deduped.append(f)

    # Only keep enabled keys that still exist in the (post-filtered) discovered set.
    # This ensures filtered-out fields (past flights, ticket numbers, long IDs) are
    # not kept enabled just because they were enabled in a previous discovery run.
    valid_keys = {f["key"] for f in deduped}
    ex["enabled_fields"]    = list((ex_enabled | {f["key"] for f in fields}) & valid_keys)
    ex["discovered_fields"] = deduped
    ex["discovered_at"]     = iso()
    # Accumulate genuinely new field keys so UI can highlight them
    if truly_new_keys and existing_keys:  # only notify when there were already fields (not first discovery)
        prev_new = ex.get("new_fields", [])
        ex["new_fields"] = list({*prev_new, *truly_new_keys})
    db.execute(
        "UPDATE account_credentials SET extra_enc=?, updated_at=? WHERE user_id=? AND source=?",
        (encrypt_cred(uid, json.dumps(ex)), iso(), uid, source)
    )
    # Update account_data items
    enabled_set = set(ex["enabled_fields"])
    ai_items = [
        {"key": f["key"], "label": f["label"], "value": f.get("value", "–")}
        for f in deduped if f.get("key") in enabled_set
    ]
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
    db.commit()


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
        new_count_badge = (
            f'<span style="font-size:11px;font-weight:700;padding:1px 7px;border-radius:99px;'
            f'background:#7c3aed;color:#fff;margin-left:6px">{len(new_keys)} new</span>'
            if new_keys else ""
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
    <button class="btn-save" onclick="saveCred('{he(key)}')">Save & Sync</button>
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
@media(max-width:768px){{html,body{{height:auto;overflow:auto}}.sidebar{{display:none}}.main-content{{height:auto;overflow:visible}}}}
</style>
</head>
<body>
{_sidebar_html('accounts', user["email"], csrf)}

<div class="main-content">
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

function saveCred(key) {{
  var u = document.getElementById('u-' + key).value.trim();
  var p = document.getElementById('p-' + key).value;
  var t = document.getElementById('t-' + key) ? document.getElementById('t-' + key).value.trim() : '';
  if (!u || !p) {{ toast('Username and password required', false); return; }}
  var saveBtn = document.querySelector('#form-' + key + ' .btn-save');
  if (saveBtn) {{ saveBtn.textContent = 'Saving & syncing...'; saveBtn.disabled = true; }}
  fetch('/credentials/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF, source: key, username: u, password: p, totp_secret: t}})
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{
      toast('Saved — syncing ' + key + '...');
      fetch('/sync/account/' + key, {{
        method: 'POST',
        headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
        body: new URLSearchParams({{_csrf: CSRF}})
      }}).then(function() {{
        var poll = setInterval(function() {{
          fetch('/sync/status').then(r => r.json()).then(function(s) {{
            if (!s.running) {{ clearInterval(poll); location.reload(); }}
          }});
        }}, 3000);
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
    var rel = fmtRelative(ts);
    if (rel) el.textContent = 'Synced ' + rel;
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
        "SELECT created_at FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    created = existing["created_at"] if existing else now

    db.execute(
        "INSERT OR REPLACE INTO account_credentials "
        "(user_id, source, username_enc, password_enc, extra_enc, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (uid, source,
         encrypt_cred(uid, username),
         encrypt_cred(uid, password) if password else "",
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

    # Run discovery 3 times and merge — maximises field coverage without user effort
    try:
        merged: dict = {}
        for _run in range(3):
            run_fields = claude_discover_fields(raw_text, site_name)
            for f in run_fields:
                k = f.get("key", "")
                if k and k not in merged:
                    merged[k] = f          # new field
                elif k and f.get("value"):
                    merged[k]["value"] = f["value"]  # refresh value
        fields = list(merged.values())
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


@app.route("/api/data/rediscover-all", methods=["POST"])
@require_login
def api_rediscover_all():
    """Re-run field discovery on ALL accounts' existing raw_text in a background thread."""
    try:
        check_csrf()
    except Exception:
        return jsonify({"ok": False, "error": "Session expired"}), 403
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
                        fields = claude_discover_fields(raw, site_name)
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
    get_db().execute(
        "DELETE FROM account_credentials WHERE user_id=? AND source=?",
        (session["user_id"], source)
    )
    get_db().commit()
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

    data       = body.get("data") or {}
    synced_at  = body.get("synced_at") or iso()
    display    = data.get("name", source)
    icon       = data.get("icon", "?")
    color      = data.get("color", "#f0f0f0")
    raw_text   = data.get("raw_text", "") or body.get("raw_text", "")
    sync_source = (body.get("sync_source") or data.get("sync_source") or "railway").lower()

    db = get_db()
    existing_row = db.execute(
        "SELECT data_enc, synced_at FROM account_data WHERE user_id=? AND source=?",
        (user["id"], source)
    ).fetchone()
    ex_data = decrypt_account_data(user["id"], existing_row["data_enc"] or "") if existing_row else {}

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
        # Don't overwrite good existing data with a failed login-page scrape
        if ex_data.get("sync_status") == "ok":
            print(f"[Mighty] Skipping login-page overwrite for {source} — good data exists", flush=True)
            return jsonify({"ok": True, "skipped": True, "reason": "login_page_but_good_data_exists"})
        data["sync_status"] = "login_required"
        data["items"] = []
        raw_text = ""
        data["raw_text"] = ""
    elif not data.get("items") and not raw_text:
        data["sync_status"] = "no_data"
    else:
        data["sync_status"] = "ok"

    data["sync_source"] = sync_source

    data_enc   = encrypt_account_data(user["id"], data)

    db = get_db()
    db.execute(
        "INSERT OR REPLACE INTO account_data "
        "(user_id, source, display_name, icon, color, data_enc, synced_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (user["id"], source, display, icon, color, data_enc, synced_at),
    )
    db.commit()

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

        if not has_prefs:
            # First-time: discover fields
            def _bg_discover():
                fields = claude_discover_fields(raw_text, site_name)
                ex2 = {}
                if cred_row and cred_row["extra_enc"]:
                    try: ex2 = json.loads(decrypt_cred(uid, cred_row["extra_enc"]))
                    except Exception: pass
                if fields:
                    enabled = [f["key"] for f in fields]
                    ex2["enabled_fields"]    = enabled
                    ex2["discovered_fields"] = fields
                    ex2["discovered_at"]     = iso()
                    ex2.pop("discovery_failed", None)
                else:
                    # Discovery ran but found nothing (e.g. got a login page)
                    ex2["discovery_failed"]  = True
                    ex2.setdefault("enabled_fields",    [])
                    ex2.setdefault("discovered_fields", [])
                new_enc = encrypt_cred(uid, json.dumps(ex2))
                with app.app_context():
                    _db = get_db()
                    _db.execute(
                        "UPDATE account_credentials SET extra_enc=?, updated_at=? "
                        "WHERE user_id=? AND source=?",
                        (new_enc, iso(), uid, source)
                    )
                    _db.commit()
            threading.Thread(target=_bg_discover, daemon=True).start()
        else:
            # Existing prefs: re-run full discovery so new fields (credits, offers, certs)
            # from freshly-scraped benefit pages are picked up and merged in.
            def _bg_refresh():
                new_fields = claude_discover_fields(raw_text, site_name)
                if not new_fields:
                    return
                with app.app_context():
                    _save_discovered_fields(uid, source, new_fields)
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

    uid = user["id"]
    db  = get_db()

    existing = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not existing:
        return jsonify({"ok": False, "error": "Account not connected"}), 404

    ad      = decrypt_account_data(uid, existing["data_enc"] or "")
    old_raw = ad.get("raw_text", "")

    # Prepend the intercepted JSON so it leads the raw_text window
    intercept_block = f"\n\n=== API RESPONSE: {url} ===\n{json_data}\n"
    combined = (intercept_block + old_raw)[:40_000]
    ad["raw_text"] = combined

    db.execute(
        "UPDATE account_data SET data_enc=?, synced_at=? WHERE user_id=? AND source=?",
        (encrypt_account_data(uid, ad), synced_at, uid, source)
    )
    db.commit()
    print(f"[Intercept] {source}: {len(json_data)} chars from {url[:80]}", flush=True)

    # Re-run field discovery in background
    if _claude:
        site_name = next((n for k, n, *_ in SUPPORTED_SITES if k == source),
                         source.replace("_", " ").title())
        def _bg():
            with app.app_context():
                fields = claude_discover_fields(combined[:10_000], site_name)
                if fields:
                    _save_discovered_fields(uid, source, fields)
                    print(f"[Intercept] {source}: {len(fields)} fields discovered", flush=True)
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

    uid = user["id"]
    db  = get_db()

    existing = db.execute(
        "SELECT data_enc FROM account_data WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    if not existing:
        return jsonify({"ok": False, "error": "Account not found"}), 404

    ad = decrypt_account_data(uid, existing["data_enc"] or "")
    old_raw = ad.get("raw_text", "")

    # Prepend new page text so benefit data is prioritised in the 40k char window
    combined = f"\n\n--- {url} ---\n{new_text}\n\n" + old_raw
    combined = combined[:40_000]
    ad["raw_text"] = combined

    db.execute(
        "UPDATE account_data SET data_enc=?, synced_at=? WHERE user_id=? AND source=?",
        (encrypt_account_data(uid, ad), synced_at, uid, source)
    )
    db.commit()
    print(f"[Supplement] {source}: appended {len(new_text)} chars from {url}", flush=True)

    if _claude:
        site_name = next((n for k, n, *_ in SUPPORTED_SITES if k == source),
                         source.replace("_", " ").title())
        def _bg():
            with app.app_context():
                fields = claude_discover_fields(combined[:10_000], site_name)
                if fields:
                    _save_discovered_fields(uid, source, fields)
                    print(f"[Supplement] {source}: {len(fields)} fields", flush=True)
        threading.Thread(target=_bg, daemon=True).start()

    _registry_report_path(source, url)
    return jsonify({"ok": True, "source": source, "chars_added": len(new_text)})


@app.route("/api/registry/report", methods=["POST"])
def registry_report():
    """Accept a {site, path} report and upsert into site_paths. No auth — paths aren't personal data."""
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
    site = request.args.get("site", "").strip().lower()
    if not site:
        return jsonify({"paths": []})
    db = get_db()
    # Decay stale entries (not seen in 30 days → lose 1 quality point)
    db.execute('''
        UPDATE site_paths
        SET quality_score = MAX(0.0, quality_score - 1.0)
        WHERE site = ?
          AND julianday('now') - julianday(last_seen) > 30
          AND quality_score > 0
    ''', (site,))
    db.commit()
    rows = db.execute('''
        SELECT path FROM site_paths
        WHERE site = ? AND quality_score > 0
        ORDER BY quality_score DESC, reporter_count DESC
        LIMIT 20
    ''', (site,)).fetchall()
    return jsonify({"paths": [r["path"] for r in rows]})


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

    session_ts = (body or {}).get("session_ts") or iso()
    sources    = (body or {}).get("sources")  # optional list to restrict which accounts
    db = get_db()
    # Only touch accounts that were actually synced in this session window (last 30 min),
    # not accounts that haven't been synced in days.
    cutoff = (
        __import__("datetime").datetime.utcnow()
        - __import__("datetime").timedelta(minutes=30)
    ).isoformat()
    if sources and isinstance(sources, list):
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
    return jsonify({"ok": True, "updated": result.rowcount})


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
            "INSERT INTO account_data (user_id, source, data_enc, synced_at) VALUES (?,?,?,?)",
            (uid, source, enc, synced_at)
        )
    db.commit()

    # Trigger AI field discovery in background
    if raw_text and _claude:
        def _discover():
            fields = claude_discover_fields(raw_text, name)
            if fields:
                _save_discovered_fields(uid, source, fields)
            else:
                _cred = db.execute(
                    "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
                    (uid, source)
                ).fetchone()
                if _cred:
                    try:
                        _ex = json.loads(decrypt_cred(uid, _cred["extra_enc"] or "") or "{}")
                        _ex["discovery_failed"] = True
                        db.execute(
                            "UPDATE account_credentials SET extra_enc=? WHERE user_id=? AND source=?",
                            (encrypt_cred(uid, json.dumps(_ex)), uid, source)
                        )
                        db.commit()
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

    rows = get_db().execute(
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
    }

    accounts = []
    for row in rows:
        source = row["source"]
        if source == "_email":
            continue
        # Only include if a username is stored
        if not row["username_enc"]:
            continue

        meta = SITE_META.get(source)
        if not meta:
            continue

        name, icon, color = meta
        accounts.append({"source": source, "name": name, "icon": icon, "color": color})

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
    Skips accounts whose raw_text hasn't changed since last discovery (hash check)."""
    if not _claude:
        return
    try:
        import hashlib
        cred_rows = get_db().execute(
            "SELECT source, extra_enc FROM account_credentials WHERE user_id=?", (uid,)
        ).fetchall()

        def _discover_one(cr):
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
                # Skip if raw_text is identical to the last discovery run —
                # UNLESS existing fields contain login-wall values (means last sync failed to log in)
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
                for _ in range(1):
                    for f in claude_discover_fields(raw_text, site_name):
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
                            try:
                                ex2 = json.loads(decrypt_cred(uid, cred_row2["extra_enc"]))
                                ex2["last_raw_hash"] = raw_hash
                                get_db().execute(
                                    "UPDATE account_credentials SET extra_enc=? WHERE user_id=? AND source=?",
                                    (encrypt_cred(uid, json.dumps(ex2)), uid, src)
                                )
                                get_db().commit()
                            except Exception:
                                pass
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
    user = get_db().execute("SELECT api_key FROM users WHERE id=?", (uid,)).fetchone()
    url  = os.environ.get("BASE_URL", "https://mighty-selfserve-production.up.railway.app")
    _sync_status[uid] = {"running": True}
    _api_key = user["api_key"]  # snapshot before thread to avoid stale sqlite3.Row

    def _do():
        try:
            import scrape as _scrape
            result = _scrape.run_sync(
                api_key=_api_key,
                mighty_url=url,
                log=lambda m: print(f"[SyncAccount:{source}] {m}", flush=True),
                only_source=source,
            )
            # Auto-discover fields after sync — no manual step needed
            if result.get("synced", 0) > 0 and _claude:
                try:
                    _sync_status[uid] = {"running": True, "step": "discovering fields"}
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
                            # Run 3x discovery and merge (same as manual discover)
                            merged: dict = {}
                            for _run in range(3):
                                for f in claude_discover_fields(raw_text, site_name):
                                    k = f.get("key", "")
                                    if k and k not in merged: merged[k] = f
                                    elif k: merged[k]["value"] = f.get("value", "")
                            fields = list(merged.values())
                            if fields:
                                _save_discovered_fields(uid, source, fields)
                except Exception as de:
                    print(f"[AutoDiscover:{source}] {de}", flush=True)
            _sync_status[uid] = {
                "running": False, "last": iso(),
                "synced": result.get("synced", 0),
                "errors": result.get("errors", 0),
            }
        except Exception as e:
            _sync_status[uid] = {"running": False, "last": iso(), "error": str(e)[:120]}
            print(f"[SyncAccount] {e}", flush=True)
    threading.Thread(target=_do, daemon=True).start()
    return jsonify({"ok": True})


@app.route("/sync/status")
@require_login
def sync_status():
    """Return current sync status for the logged-in user."""
    uid    = session["user_id"]
    status = _sync_status.get(uid, {})
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



# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Start cloud sync scheduler if running on Railway
    if os.environ.get("ENABLE_CLOUD_SYNC", "").lower() == "true":
        _start_cloud_scheduler()
    app.run(host="0.0.0.0", port=PORT, debug=False)
