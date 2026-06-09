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

import os, io, csv, json, secrets, hashlib, sqlite3, threading, urllib.request, urllib.error, html, time, base64

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
        """)
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

DISCOVER_PROMPT = """You are analyzing a page from a user's {site} account.

Page text:
{text}

Identify data fields useful to monitor in a personal dashboard.
Return ONLY a JSON array, no other text:
[{{"key":"balance","label":"Current Balance","value":"$2,472.20"}}]

Rules:
- key: 1-2 word snake_case ONLY — "balance" not "current_balance", "due_date" not "payment_due_date"
- label: 2-4 words, human-readable
- value: exact current value from the page
- Each CONCEPT appears EXACTLY ONCE — do not split one idea into multiple fields
- Include: amounts, dates, points/miles, usage, alerts, enrollment status
- Skip: navigation, help text, marketing, version numbers, account holder name
- Max 8 fields"""

def claude_discover_fields(raw_text: str, site_name: str) -> list:
    """Use Gemini Flash to identify all useful data fields in a page."""
    if not _claude or not raw_text:
        return []
    try:
        prompt = DISCOVER_PROMPT.format(site=site_name, text=raw_text[:6000])
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
                return result
            # Handle {"fields": [...]} or similar wrapper
            if isinstance(result, dict):
                for k in ("fields", "data", "items", "results"):
                    if isinstance(result.get(k), list):
                        return result[k]
            return []
        except json.JSONDecodeError:
            m = re.search(r'\[.*\]', text, re.DOTALL)
            if m:
                try: return json.loads(m.group())
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
body{font-family:'Inter',sans-serif;background:#f8f7f5;color:#1a1a1a;min-height:100vh}
a{color:#7c3aed;text-decoration:none}
a:hover{text-decoration:underline}
input{font-family:inherit}
button{font-family:inherit;cursor:pointer}
.badge{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:20px;letter-spacing:0.3px}
.badge-logged{background:#f3f4f6;color:#6b7280}
.badge-pending{background:#fef3c7;color:#d97706}
.badge-approved{background:#f0fdf4;color:#16a34a}
.badge-denied{background:#fef2f2;color:#dc2626}
.badge-timeout{background:#f3f4f6;color:#9ca3af}
"""

LANDING_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="description" content="Mighty adds approval checkpoints and a permanent activity log to any AI agent. Works with Claude, ChatGPT, and custom agents. Set up in 5 minutes. Free to start.">
<title>Mighty — Your AI agents, accountable to you.</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
html{scroll-behavior:smooth}
body{background:#fff;color:#1a1a1a}
/* Nav */
.nav{position:sticky;top:0;z-index:100;background:#fff;border-bottom:1px solid #e5e3df;height:60px;display:flex;align-items:center;padding:0 24px}
.nav-inner{max-width:900px;margin:0 auto;width:100%;display:flex;align-items:center;justify-content:space-between}
.logo{display:flex;align-items:center;gap:10px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:32px;width:auto}
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
<title>Create account — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;overflow-y:auto;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:32px;width:auto}
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
<title>Sign in — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;overflow-y:auto;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:32px;width:auto}
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
<title>Reset password — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;overflow-y:auto;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:32px;width:auto}
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
<title>Set new password — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;overflow-y:auto;padding:24px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:16px;padding:40px;width:100%;max-width:400px;box-shadow:0 4px 24px rgba(0,0,0,0.06)}
.logo{display:flex;align-items:center;gap:10px;margin-bottom:28px}
.logo-mark{width:32px;height:32px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:32px;width:auto}
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
<title>Privacy — Mighty</title>
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
<title>Terms of Service — Mighty</title>
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
<title>Page not found — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:24px;background:#f8f7f5}
.wrap{text-align:center;max-width:380px}
.logo{display:flex;align-items:center;gap:10px;justify-content:center;margin-bottom:32px}
.logo-mark img{height:32px;width:auto}
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
<title>Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;flex-direction:column;height:100vh;overflow:hidden;background:#f8f7f5}
.topbar{background:#fff;border-bottom:1px solid #e5e3df;padding:0 24px;height:52px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.topbar-logo{display:flex;align-items:center;gap:8px}
.topbar-logo-mark{width:26px;height:26px;display:flex;align-items:center;justify-content:center}
.topbar-logo-mark img{height:26px;width:auto}
.topbar-name{font-size:14px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.topbar-right{display:flex;align-items:center;gap:16px}
.topbar-email{font-size:12px;color:#9ca3af;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn-logout{font-size:12px;color:#6b7280;background:none;border:none;cursor:pointer;padding:4px 8px;border-radius:5px;transition:background 0.12s}
.btn-logout:hover{background:#f3f4f6;color:#1a1a1a}
.main{flex:1;min-height:0;display:grid;grid-template-columns:320px 1fr;gap:24px;max-width:1140px;width:100%;margin:0 auto;padding:28px 24px;box-sizing:border-box}
@media(max-width:768px){.main{grid-template-columns:1fr}}
.sidebar{display:flex;flex-direction:column;gap:14px;overflow-y:auto}
.feed-col{overflow-y:auto;min-height:0;padding-bottom:28px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:12px;padding:20px}
.setup-heading{font-size:14px;font-weight:700;color:#1a1a1a;margin-bottom:14px}
.tab-bar{display:flex;gap:4px;background:#f3f4f6;border-radius:8px;padding:3px;margin-bottom:16px}
.tab{flex:1;padding:6px 10px;border:none;background:none;border-radius:6px;font-size:12px;font-weight:600;color:#6b7280;cursor:pointer;transition:all 0.12s}
.tab.active{background:#fff;color:#1a1a1a;box-shadow:0 1px 3px rgba(0,0,0,0.1)}
.tab-content{display:none}.tab-content.active{display:block}
.step{display:flex;gap:10px;margin-bottom:14px}
.step-num{width:20px;height:20px;border-radius:50%;background:#f3f0ff;color:#7c3aed;font-size:11px;font-weight:700;display:flex;align-items:center;justify-content:center;flex-shrink:0;margin-top:1px}
.step-body{min-width:0;flex:1}
.step-title{font-size:13px;font-weight:600;color:#1a1a1a;margin-bottom:4px}
.step-hint{font-size:12px;color:#6b7280;line-height:1.5;margin-top:4px}
.code-box{font-family:ui-monospace,monospace;font-size:10px;color:#6b7280;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:10px;white-space:pre;overflow-x:auto;margin:6px 0;max-width:100%;box-sizing:border-box}
.path-box{font-family:ui-monospace,monospace;font-size:10px;color:#7c3aed;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:10px;word-break:break-all;line-height:1.5;margin:6px 0}
.btn-action{width:100%;padding:10px;background:#7c3aed;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;transition:background 0.12s;cursor:pointer;margin-bottom:4px}
.btn-action:hover{background:#6d28d9}
.btn-secondary{width:100%;padding:9px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:8px;font-size:13px;font-weight:600;transition:background 0.12s;cursor:pointer;text-decoration:none;display:block;text-align:center}
.btn-secondary:hover{background:#ede9fe;text-decoration:none}
details{margin-top:12px}
summary{font-size:12px;color:#6b7280;cursor:pointer;user-select:none;list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"\\25B8 "}
details[open] summary::before{content:"\\25BE "}
.api-key-wrap{margin-top:10px;display:flex;align-items:center;gap:8px}
.api-key-val{flex:1;font-family:ui-monospace,monospace;font-size:10px;color:#6b7280;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:7px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn-copy-key{font-size:12px;font-weight:600;padding:5px 10px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;white-space:nowrap;cursor:pointer;transition:background 0.12s}
.btn-copy-key:hover{background:#ede9fe}
.status-row{display:flex;align-items:center;gap:12px;padding:2px 0}
.status-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0;margin-top:2px}
.status-green{background:#16a34a;box-shadow:0 0 0 3px #dcfce7}
.status-title{font-size:14px;font-weight:600;color:#1a1a1a}
.status-sub{font-size:12px;color:#6b7280;margin-top:2px}
.prompt-hidden{display:none}
.feed-title{font-size:16px;font-weight:700;color:#1a1a1a;margin-bottom:4px}
.feed-sub{font-size:12px;color:#6b7280;margin-bottom:20px}
.feed{display:flex;flex-direction:column;gap:10px}
.pending-label{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:0.8px;color:#7c3aed;display:flex;align-items:center;gap:6px;margin-bottom:10px}
.pending-dot{width:6px;height:6px;border-radius:50%;background:#7c3aed;animation:pulse 1.5s infinite;flex-shrink:0}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.action-card{background:#fff;border:1px solid #e5e3df;border-radius:10px;overflow:hidden}
.action-card:hover{box-shadow:0 2px 10px rgba(0,0,0,0.06)}
.action-card.is-pending{border-color:#fbbf24}
.action-top{padding:14px 16px 0;display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
.action-label{font-size:14px;font-weight:600;color:#1a1a1a;line-height:1.4}
.action-type{font-size:11px;color:#9ca3af;font-family:ui-monospace,monospace;margin-top:2px}
.action-badges{display:flex;align-items:center;gap:6px;flex-shrink:0;flex-wrap:wrap;justify-content:flex-end}
.action-time{font-size:11px;color:#9ca3af;margin-top:4px;text-align:right}
.action-fields{padding:10px 16px 14px;display:flex;flex-direction:column;gap:5px}
.field-row{display:flex;gap:10px;font-size:12px}
.field-key{color:#9ca3af;font-weight:600;text-transform:uppercase;font-size:11px;letter-spacing:0.5px;min-width:80px;flex-shrink:0;padding-top:1px}
.field-val{color:#374151;line-height:1.4;word-break:break-word}
.action-buttons{padding:12px 16px;border-top:1px solid #f0ede8;display:flex;gap:8px}
.btn-authorize{flex:1;padding:9px;background:#16a34a;color:#fff;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:background 0.12s}
.btn-authorize:hover{background:#15803d}
.btn-reject{flex:1;padding:9px;background:#fff;color:#dc2626;border:1.5px solid #fecaca;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all 0.12s}
.btn-reject:hover{background:#fef2f2}
.clevel-sensitive{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:#eff6ff;color:#2563eb;letter-spacing:0.3px}
.clevel-consequential{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:#fffbeb;color:#d97706;letter-spacing:0.3px}
.clevel-critical{display:inline-block;font-size:11px;font-weight:600;padding:2px 7px;border-radius:20px;background:#fef2f2;color:#dc2626;letter-spacing:0.3px}
.empty-state{text-align:center;padding:40px 20px}
.empty-state-icon{width:40px;height:40px;background:#f3f0ff;border-radius:10px;display:flex;align-items:center;justify-content:center;margin:0 auto 16px}
.empty-state-title{font-size:14px;font-weight:600;color:#6b7280;margin-bottom:6px}
.empty-state-sub{font-size:12px;color:#9ca3af;line-height:1.6}
.feed-tab{padding:6px 14px;border-radius:7px;border:1px solid #e5e3df;background:#fff;font-size:12px;font-weight:600;color:#6b7280;cursor:pointer;font-family:inherit;transition:all 0.12s}
.feed-tab.active{background:#f3f0ff;border-color:#d4c6ff;color:#5b21b6}
.acct-card{background:#fff;border:1px solid #e5e3df;border-radius:12px;padding:16px 18px;margin-bottom:10px}
.acct-icon{width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0}
.acct-row{display:flex;justify-content:space-between;align-items:baseline;padding:5px 0;border-bottom:1px solid #f5f3f0}
.acct-row:last-child{border-bottom:none}
.acct-lbl{font-size:12px;color:#9ca3af}
.acct-val{font-size:13px;font-weight:600;color:#1a1a1a}
@media(max-width:768px){body{height:auto;overflow:auto}.feed-col{overflow:visible}.sidebar{overflow:visible}}
</style>
</head>
<body>
<div class="topbar">
  <a href="/dashboard" style="text-decoration:none;display:flex;align-items:center;gap:8px">
    <div class="topbar-logo-mark">
      <img src="/logo-icon.png" alt="Mighty">
    </div>
    <span class="topbar-name">Mighty</span>
  </a>
  <div id="pending-badge" style="display:{pending_display};background:#f3f0ff;border:1px solid #e9d5ff;border-radius:20px;padding:4px 12px;font-size:12px;font-weight:600;color:#5b21b6">
    {pending_count} awaiting decision
  </div>
  <div class="topbar-right">
    <a href="/credentials" style="font-size:12px;color:#6b7280;text-decoration:none">Accounts</a>
    <a href="/settings" style="font-size:12px;color:#6b7280;text-decoration:none">Settings</a>
    <span class="topbar-email">{email}</span>
    <form method="POST" action="/logout" style="margin:0"><input type="hidden" name="_csrf" value="{csrf_token}"><button class="btn-logout" type="submit">Sign out</button></form>
  </div>
</div>

{onboarding_banner}
<div class="main">
  {sidebar_content}

  <div class="feed-col" {feed_col_hidden}>
    <div style="display:flex;gap:6px;margin-bottom:18px">
      <button class="feed-tab active" id="ftab-activity" onclick="switchFeedTab('activity',this)">Activity Log</button>
      <button class="feed-tab" id="ftab-accounts" onclick="switchFeedTab('accounts',this)">Account Data</button>
    </div>

    <div id="fview-activity">
      <div class="feed-title">Activity Log</div>
      <div class="feed-sub">A live log of everything your agent does</div>
      <div style="margin-bottom:14px">
        <input type="text" id="feed-search" placeholder="Filter actions…" oninput="filterFeed(this.value)" style="width:100%;padding:8px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:13px;font-family:inherit;outline:none;color:#1a1a1a;background:#fff;transition:border-color 0.12s" onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#e5e3df'">
      </div>
      <div class="feed" id="feed">
        {feed_html}
      </div>
    </div>

    <div id="fview-accounts" style="display:none">
      <div class="feed-title">Account Data</div>
      <div class="feed-sub">Live data pulled from your connected accounts</div>
      <div style="margin-top:14px">{account_data_html}</div>
    </div>
  </div>
</div>

<script>
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
  sessionStorage.setItem('mighty-feed-tab', name);
}
// Restore feed tab after reload
(function() {
  var t = sessionStorage.getItem('mighty-feed-tab');
  if (t && t !== 'activity') {
    var btn = document.getElementById('ftab-' + t);
    if (btn) switchFeedTab(t, btn);
  }
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
function copyKey(btn) {
  navigator.clipboard.writeText(document.getElementById('apiKeyVal').textContent.trim());
  btn.textContent = 'Copied!';
  setTimeout(() => btn.textContent = 'Copy', 1800);
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
// Restore feed scroll position after auto-reload
(function() {
  var fc = document.querySelector('.feed-col');
  var saved = sessionStorage.getItem('mighty-feed-scroll');
  if (fc && saved) { fc.scrollTop = parseInt(saved); sessionStorage.removeItem('mighty-feed-scroll'); }
  var sy = sessionStorage.getItem('mighty-scroll-y');
  if (sy) { window.scrollTo(0, parseInt(sy)); sessionStorage.removeItem('mighty-scroll-y'); }
})();

function filterFeed(q) {
  q = (q || '').toLowerCase();
  document.querySelectorAll('.action-card').forEach(function(card) {
    card.style.display = card.textContent.toLowerCase().includes(q) ? '' : 'none';
  });
}

function toggleDetail(id) {
  var el = document.getElementById('detail-' + id);
  if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
}

var lastPending = document.querySelectorAll('.is-pending').length > 0;
function checkForUpdates() {
  fetch('/dashboard/has-pending').then(function(r) { return r.json(); }).then(function(d) {
    if (d.pending !== lastPending) {
      var fc = document.querySelector('.feed-col');
      if (fc) sessionStorage.setItem('mighty-feed-scroll', fc.scrollTop);
      sessionStorage.setItem('mighty-scroll-y', window.scrollY || document.documentElement.scrollTop || 0);
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
<title>Get started — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:#f8f7f5;color:#1a1a1a;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:flex-start;padding:20px 24px}
.wrap{width:100%;max-width:480px;display:flex;flex-direction:column;min-height:calc(100vh - 40px)}
.logo{display:flex;align-items:center;gap:8px;justify-content:center;margin-bottom:16px;flex-shrink:0}
.logo-mark{width:28px;height:28px;display:flex;align-items:center;justify-content:center}
.logo-mark img{height:28px;width:auto}
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
<title>Settings — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;flex-direction:column;height:100vh;overflow:hidden;background:#f8f7f5}
@media(max-width:768px){body{height:auto;overflow:auto}.settings-body{overflow:visible}}
.topbar{background:#fff;border-bottom:1px solid #e5e3df;padding:0 24px;height:52px;display:flex;align-items:center;justify-content:space-between;flex-shrink:0}
.topbar-logo{display:flex;align-items:center;gap:8px}
.topbar-logo-mark{width:26px;height:26px;display:flex;align-items:center;justify-content:center}
.topbar-logo-mark img{height:26px;width:auto}
.topbar-name{font-size:14px;font-weight:800;letter-spacing:0.5px;background:linear-gradient(135deg,#7c3aed,#6d28d9);-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}
.topbar-right{display:flex;align-items:center;gap:16px}
.topbar-email{font-size:12px;color:#9ca3af;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn-logout{font-size:12px;color:#6b7280;background:none;border:none;cursor:pointer;padding:4px 8px;border-radius:5px;transition:background 0.12s}
.btn-logout:hover{background:#f3f4f6;color:#1a1a1a}
.settings-body{flex:1;min-height:0;overflow-y:auto}
.settings-wrap{max-width:520px;margin:0 auto;padding:32px 24px;display:flex;flex-direction:column;gap:16px}
.page-title{font-size:18px;font-weight:700;color:#1a1a1a;margin-bottom:4px}
.card{background:#fff;border:1px solid #e5e3df;border-radius:12px;padding:24px}
.section-title{font-size:14px;font-weight:700;color:#1a1a1a;margin-bottom:16px}
.toggle-row{display:flex;align-items:flex-start;gap:12px;padding:12px 0}
.toggle-row+.toggle-row{border-top:1px solid #f3f4f6}
.toggle-label{font-size:13px;font-weight:600;color:#1a1a1a;margin-bottom:3px}
.toggle-hint{font-size:12px;color:#6b7280;line-height:1.5}
.api-key-wrap{display:flex;align-items:center;gap:8px;margin-top:4px}
.api-key-val{flex:1;font-family:ui-monospace,monospace;font-size:12px;color:#6b7280;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:8px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.btn-copy-key{font-size:12px;font-weight:600;padding:6px 12px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;white-space:nowrap;cursor:pointer;transition:background 0.12s}
.btn-copy-key:hover{background:#ede9fe}
.push-status{font-size:12px;color:#6b7280;margin-top:6px;min-height:16px}
.push-btn{font-size:12px;font-weight:600;padding:6px 12px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;cursor:pointer;transition:background 0.12s;display:none;margin-top:6px}
.push-btn:hover{background:#ede9fe}
.btn-danger{font-size:12px;font-weight:600;padding:8px 12px;background:#fff;color:#dc2626;border:1.5px solid #fecaca;border-radius:6px;white-space:nowrap;cursor:pointer;transition:all 0.12s;width:100%;text-align:left}
.btn-danger:hover{background:#fef2f2}
.ntfy-link{display:inline-block;margin-top:6px;font-size:12px;font-family:ui-monospace,monospace;color:#7c3aed;background:#f8f7f5;border:1px solid #e5e3df;border-radius:6px;padding:6px 10px;text-decoration:none;word-break:break-all}
</style>
</head>
<body>
<div class="topbar">
  <a href="/dashboard" style="text-decoration:none;display:flex;align-items:center;gap:8px">
    <div class="topbar-logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
    <span class="topbar-name">Mighty</span>
  </a>
  <div class="topbar-right">
    <a href="/dashboard" style="font-size:12px;color:#6b7280;text-decoration:none">&#8592; Dashboard</a>
    <span class="topbar-email">{email}</span>
    <form method="POST" action="/logout" style="margin:0"><input type="hidden" name="_csrf" value="{csrf_token}"><button class="btn-logout" type="submit">Sign out</button></form>
  </div>
</div>

<div class="settings-body">
  <div class="settings-wrap">
    <div class="page-title">Settings</div>

    <div class="card">
      <div class="section-title">Notifications</div><span id="save-ind" style="font-size:11px;color:#16a34a;margin-left:8px;display:none">Saved ✓</span>
      <div class="toggle-row">
        <input type="checkbox" id="notif-push" {push_checked} onchange="save()" style="width:16px;height:16px;accent-color:#7c3aed;flex-shrink:0;margin-top:2px">
        <div>
          <div class="toggle-label">Browser alerts</div>
          <div class="toggle-hint">Desktop popup when your agent needs a decision. Click Allow notifications below to activate.</div>
          <div id="push-status" class="push-status"></div>
          <button id="push-enable-btn" class="push-btn" onclick="enablePush()">Allow notifications &#8594;</button>
        </div>
      </div>
      <div class="toggle-row">
        <input type="checkbox" id="notif-ntfy" {ntfy_checked} onchange="save()" style="width:16px;height:16px;accent-color:#7c3aed;flex-shrink:0;margin-top:2px">
        <div>
          <div class="toggle-label">Mobile alerts (ntfy)</div>
          <div class="toggle-hint">Install the free <a href="https://ntfy.sh" target="_blank" style="color:#7c3aed">ntfy app</a>, then subscribe to your channel on your phone.</div>
          <a href="https://ntfy.sh/{ntfy_topic}" target="_blank" class="ntfy-link">ntfy.sh/{ntfy_topic} &#8599;</a>
          <div style="font-size:11px;color:#9ca3af;margin-top:6px">Only action labels and approval links are sent — no account data.</div>
        </div>
      </div>
      <div class="toggle-row">
        <input type="checkbox" id="notif-email" {email_checked} onchange="onEmailToggle()" style="width:16px;height:16px;accent-color:#7c3aed;flex-shrink:0;margin-top:2px">
        <div>
          <div class="toggle-label">Email alerts</div>
          <div class="toggle-hint">Receive an email when your agent requests approval.</div>
          <div id="email-notif-warn" style="display:{postmark_warn};margin-top:6px;font-size:12px;color:#d97706;background:#fffbeb;border:1px solid #fde68a;border-radius:6px;padding:6px 10px;line-height:1.5">Email alerts require the POSTMARK_API_KEY environment variable to be set on your server.</div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Account</div>
      <div style="font-size:13px;color:#6b7280;margin-bottom:16px">Signed in as <span style="color:#1a1a1a;font-weight:600">{email}</span></div>
      <div style="font-size:12px;font-weight:600;color:#555;margin-bottom:6px;letter-spacing:0.3px">Change email address</div>
      <input type="email" id="email-new" placeholder="New email address" style="width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;margin-bottom:10px;outline:none;font-family:inherit;color:#1a1a1a;background:#fff;transition:border-color 0.12s" onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#e5e3df'">
      <input type="password" id="email-pw" placeholder="Confirm with current password" style="width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;margin-bottom:10px;outline:none;font-family:inherit;color:#1a1a1a;background:#fff;transition:border-color 0.12s" onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#e5e3df'">
      <div style="display:flex;align-items:center;gap:12px">
        <button class="btn-copy-key" style="padding:8px 16px;font-size:13px" onclick="changeEmail()">Update email</button>
        <span id="email-msg" style="font-size:12px;display:none"></span>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Privacy</div>
      <div class="toggle-row">
        <input type="checkbox" id="minimal-logging" {minimal_logging_checked} onchange="savePrivacy()" style="width:16px;height:16px;accent-color:#7c3aed;flex-shrink:0;margin-top:2px">
        <div>
          <div class="toggle-label">Minimal logging</div>
          <div class="toggle-hint">Store only action type and timestamp — not labels or field details. Reduces what Mighty can see, but makes your activity log less useful.</div>
          <span id="privacy-ind" style="display:none;font-size:12px;color:#16a34a;margin-top:4px">Saved</span>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Connection</div>
      <div style="font-size:12px;color:#6b7280;margin-bottom:8px">Your API key — used to connect your agent to Mighty. Keep it secret.</div>
      <div class="api-key-wrap">
        <div class="api-key-val" id="apiKeyVal">{api_key}</div>
        <button class="btn-copy-key" onclick="copyKey(this)">Copy</button>
      </div>
      <div style="font-size:12px;color:#9ca3af;margin-top:6px">Anyone with this key can submit actions on your behalf.</div>
      <div style="margin-top:16px;padding-top:16px;border-top:1px solid #f3f4f6">
        <a href="/onboarding" style="display:inline-block;margin-top:4px;padding:8px 14px;background:#f3f0ff;color:#7c3aed;border:1px solid #e9d5ff;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none">&#8635; Re-run setup</a>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Security</div>
      <div style="font-size:13px;color:#6b7280;margin-bottom:16px">Change your account password.</div>
      <label style="display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px">Current password</label>
      <input type="password" id="pw-current" placeholder="Your current password" style="width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;margin-bottom:12px;outline:none;font-family:inherit;color:#1a1a1a;background:#fff;transition:border-color 0.12s" onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#e5e3df'">
      <label style="display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px">New password</label>
      <input type="password" id="pw-new" placeholder="At least 6 characters" style="width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;margin-bottom:12px;outline:none;font-family:inherit;color:#1a1a1a;background:#fff;transition:border-color 0.12s" onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#e5e3df'">
      <label style="display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:5px;letter-spacing:0.3px">Confirm new password</label>
      <input type="password" id="pw-confirm" placeholder="Repeat new password" style="width:100%;padding:10px 12px;border:1.5px solid #e5e3df;border-radius:8px;font-size:14px;margin-bottom:12px;outline:none;font-family:inherit;color:#1a1a1a;background:#fff;transition:border-color 0.12s" onfocus="this.style.borderColor='#7c3aed'" onblur="this.style.borderColor='#e5e3df'">
      <div style="display:flex;align-items:center;gap:12px">
        <button class="btn-copy-key" style="padding:8px 16px;font-size:13px" onclick="changePassword()">Update password</button>
        <span id="pw-msg" style="font-size:12px;display:none"></span>
      </div>
    </div>

    <div class="card">
      <div class="section-title">Data &amp; Privacy</div>
      <button class="btn-copy-key" onclick="window.location.href='/settings/export-csv'">&#8595; Export activity log (CSV)</button>
      <hr style="border:none;border-top:1px solid #f3f4f6;margin:16px 0">
      <div style="font-size:12px;font-weight:700;color:#dc2626;margin-bottom:10px;text-transform:uppercase;letter-spacing:0.5px">Danger zone</div>
      <div style="display:flex;flex-direction:column;gap:10px">
        <button class="btn-danger" id="del-activity-btn" onclick="deleteActivity()">Delete all activity</button>
        <span id="del-activity-msg" style="font-size:12px;color:#16a34a;display:none">All activity deleted.</span>
      </div>
      <hr style="border:none;border-top:1px solid #fecaca;margin:16px 0">
      <div style="font-size:13px;color:#6b7280;margin-bottom:12px;line-height:1.5">Permanently deletes your account and all data. This cannot be undone.</div>
      <div id="del-acct-btn-wrap">
        <button class="btn-danger" onclick="showDelConfirm()">Delete my account</button>
      </div>
      <div id="del-acct-confirm" style="display:none">
        <label style="display:block;font-size:12px;font-weight:600;color:#555;margin-bottom:6px">Confirm with your current password</label>
        <input type="password" id="del-acct-pw" placeholder="Your password" style="width:100%;padding:10px 12px;border:1.5px solid #fecaca;border-radius:8px;font-size:14px;margin-bottom:10px;outline:none;font-family:inherit;color:#1a1a1a">
        <div style="display:flex;gap:8px">
          <button class="btn-danger" style="flex:1" onclick="deleteAccount()">Confirm deletion</button>
          <button onclick="hideDelConfirm()" style="padding:8px 14px;background:#f3f4f6;border:none;border-radius:6px;font-size:13px;font-weight:600;color:#6b7280;cursor:pointer">Cancel</button>
        </div>
        <div id="del-acct-err" style="font-size:12px;color:#dc2626;margin-top:8px;display:none"></div>
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
function copyKey(btn) {
  navigator.clipboard.writeText(document.getElementById('apiKeyVal').textContent.trim());
  btn.textContent = 'Copied!';
  setTimeout(function() { btn.textContent = 'Copy'; }, 1800);
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
<meta name="theme-color" content="#7c3aed">
<title>Authorize action — Mighty</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
""" + BASE_CSS + """
body{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;background:#f8f7f5}
.wrap{width:100%;max-width:480px}
.brand{display:flex;align-items:center;gap:8px;margin-bottom:20px;justify-content:center}
.brand-mark{width:28px;height:28px;display:flex;align-items:center;justify-content:center}
.brand-mark img{height:28px;width:auto}
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

@app.route("/logout", methods=["POST"])
def logout():
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
            f'<div id="detail-{aid}" style="display:none;padding:6px 16px 12px;border-top:1px solid #f3f4f6">'
            + "".join(f'<span style="font-size:11px;color:#9ca3af;background:#f8f7f5;border-radius:4px;padding:2px 7px;margin-right:6px;display:inline-block">{e}</span>' for e in extra)
            + '</div>'
        )
    detail_toggle = (
        f'<button onclick="toggleDetail(\'{aid}\')" style="font-size:11px;color:#9ca3af;'
        'background:none;border:none;cursor:pointer;padding:0;margin-left:4px">details</button>'
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
            '<div style="grid-column:1/-1;background:#f3f0ff;border:1px solid #e9d5ff;'
            'border-radius:10px;padding:14px 18px;display:flex;align-items:center;'
            'justify-content:space-between;gap:16px;margin-bottom:16px">'
            '<div style="font-size:13px;color:#5b21b6">'
            'Finish setting up Mighty to connect your first agent.</div>'
            '<a href="/onboarding" style="font-size:13px;font-weight:600;color:#7c3aed;white-space:nowrap">'
            'Complete setup &#8594;</a></div>'
        )

    if len(acts) == 0:
        if user["onboarded"]:
            # Real dashboard layout — sidebar + empty feed
            sidebar_card = (
                '<div class="card">'
                '<div class="status-row">'
                '<div class="status-dot status-green"></div>'
                '<div>'
                '<div class="status-title">Mighty is ready</div>'
                '<div class="status-sub">Waiting for your first request</div>'
                '</div></div>'
                '</div>'
            )
            sidebar_content = '<div class="sidebar">' + sidebar_card + '</div>'
            feed = (
                '<div style="display:flex;flex-direction:column;align-items:center;'
                'justify-content:center;padding:60px 24px;text-align:center">'
                '<div style="font-size:14px;font-weight:600;color:#9ca3af;margin-bottom:8px">No requests yet</div>'
                '<div style="font-size:13px;color:#b0b8c4;line-height:1.6;max-width:280px">'
                'Ask your agent to do something that needs approval and the request will appear here.</div>'
                '</div>'
            )
            feed_col_hidden = ''
        else:
            # Not yet onboarded — full-width welcome
            sidebar_content = (
                '<div style="grid-column:1/-1;display:flex;flex-direction:column;'
                'align-items:center;justify-content:center;padding:60px 24px">'
                '<div style="width:100%;max-width:360px;text-align:center">'
                '<div style="width:52px;height:52px;background:#f3f0ff;border-radius:14px;'
                'display:flex;align-items:center;justify-content:center;margin:0 auto 20px">'
                '<svg width="22" height="22" fill="none" stroke="#7c3aed" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
                '<polygon points="13,2 3,14 12,14 11,22 21,10 12,10"/></svg></div>'
                '<div style="font-size:22px;font-weight:700;color:#1a1a1a;margin-bottom:10px">'
                'Welcome to Mighty</div>'
                '<div style="font-size:14px;color:#6b7280;line-height:1.6;margin-bottom:28px">'
                'Connect your agent in about 5 minutes. Once connected, approval requests from your agent will appear here.</div>'
                '<a href="/onboarding" style="display:block;padding:13px 20px;'
                'background:#7c3aed;color:#fff;border-radius:8px;font-size:14px;font-weight:600;'
                'text-decoration:none;margin-bottom:16px">Get started &#8594;</a>'
                '</div>'
                '</div>'
            )
            feed_col_hidden = 'style="display:none"'
    else:
        # Active state: two-column layout
        if is_connected:
            sidebar_card = (
                '<div class="card">'
                '<div class="status-row">'
                '<div class="status-dot status-green"></div>'
                '<div>'
                '<div class="status-title">Mighty is active</div>'
                '<div class="status-sub">Your agent is connected</div>'
                '</div></div>'
                '<div style="margin-top:14px;padding-top:14px;border-top:1px solid #f0ede8">'
                '<a href="/onboarding" class="btn-secondary" style="display:block;text-align:center;padding:9px;font-size:13px;font-weight:600;text-decoration:none">'
                'Connect another agent</a>'
                '</div>'
                '</div>'
            )
        else:
            sidebar_card = (
                '<div class="card">'
                '<div class="setup-heading">Connect your agent</div>'
                '<p style="font-size:13px;color:#6b7280;line-height:1.5;margin-bottom:14px">'
                'Run the setup wizard to connect Claude Desktop, ChatGPT, or a custom agent.</p>'
                '<a href="/onboarding" style="display:block;text-align:center;padding:10px;'
                'background:#7c3aed;color:#fff;border-radius:8px;font-size:13px;font-weight:600;'
                'text-decoration:none">Set up another agent &#8594;</a>'
                '</div>'
            )
        sidebar_content = '<div class="sidebar">' + sidebar_card + '</div>'
        feed_col_hidden = ''

    # ── Account data tab ──────────────────────────────────────────────────────
    acct_rows = get_db().execute(
        "SELECT * FROM account_data WHERE user_id=? ORDER BY synced_at DESC",
        (user["id"],)
    ).fetchall()

    CATEGORY_ORDER = ["amex","chase","sfcu","amazon","delta","hertz","marriott",
                      "hilton","disney_plus","ticketmaster","xfinity","pa_utilities","pamf"]

    def _fmt_sync(ts):
        try:
            dt = datetime.fromisoformat(ts)
            delta = utcnow() - dt
            mins = int(delta.total_seconds() // 60)
            if mins < 60: return f"{mins}m ago"
            hrs = mins // 60
            if hrs < 24: return f"{hrs}h ago"
            return f"{hrs // 24}d ago"
        except Exception:
            return ts[:10] if ts else "—"

    if acct_rows:
        cards_html = ""
        row_map = {r["source"]: r for r in acct_rows}
        ordered = [row_map[k] for k in CATEGORY_ORDER if k in row_map]
        ordered += [r for r in acct_rows if r["source"] not in CATEGORY_ORDER]
        # Load field preferences for filtering
        field_prefs: dict = {}
        cred_rows = get_db().execute(
            "SELECT source, extra_enc FROM account_credentials WHERE user_id=?",
            (user["id"],)
        ).fetchall()
        for cr in cred_rows:
            if cr["extra_enc"]:
                try:
                    ex = json.loads(decrypt_cred(user["id"], cr["extra_enc"]))
                    if "enabled_fields" in ex:
                        field_prefs[cr["source"]] = set(ex["enabled_fields"])
                except Exception:
                    pass

        for row in ordered:
            data  = decrypt_account_data(user["id"], row["data_enc"] or "")
            items = data.get("items", [])
            # Filter by user field preferences if configured for this source
            enabled = field_prefs.get(row["source"])
            # If Claude discovered fields, filter to enabled ones
            if enabled is not None:
                items = [i for i in items
                         if i.get("key") in enabled or not i.get("key")]
            items_html = "".join(
                f'<div class="acct-row"><span class="acct-lbl">{he(i["label"])}</span>'
                f'<span class="acct-val">{he(i["value"])}</span></div>'
                for i in items
            ) if items else '<div class="acct-row"><span class="acct-lbl" style="font-style:italic">No data extracted</span></div>'
            status_color = "#30d158" if data.get("status") == "ok" else "#ff3b30"
            cards_html += (
                f'<div class="acct-card">'
                f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">'
                f'<div class="acct-icon" style="background:{he(row["color"] or "#f0f0f0")}">{he(row["icon"] or "?")}</div>'
                f'<div style="flex:1">'
                f'<div style="font-size:13px;font-weight:600;color:#1a1a1a">{he(row["display_name"])}</div>'
                f'<div style="font-size:11px;color:#9ca3af">Synced {_fmt_sync(row["synced_at"])}</div>'
                f'</div>'
                f'<div style="width:8px;height:8px;border-radius:50%;background:{status_color};flex-shrink:0"></div>'
                f'</div>'
                f'{items_html}'
                f'</div>'
            )
        account_data_html = cards_html
    else:
        account_data_html = (
            '<div style="text-align:center;padding:48px 24px">'
            '<div style="font-size:14px;font-weight:600;color:#6b7280;margin-bottom:8px">No account data yet</div>'
            '<div style="font-size:13px;color:#9ca3af;line-height:1.6;max-width:280px;margin:0 auto">'
            'Run <code style="background:#f3f0ff;padding:2px 6px;border-radius:4px;font-size:12px">python3 scrape.py</code> '
            'with your Mighty API key set to sync your accounts here.</div>'
            '</div>'
        )

    return (DASHBOARD_HTML
            .replace("{email}",              he(user["email"]))
            .replace("{feed_html}",          feed)
            .replace("{pending_count}",      str(pending_count))
            .replace("{pending_display}",    pending_display)
            .replace("{sidebar_content}",    sidebar_content)
            .replace("{feed_col_hidden}",    feed_col_hidden)
            .replace("{onboarding_banner}",  onboarding_banner)
            .replace("{account_data_html}",  account_data_html)
            .replace("{csrf_token}",         get_csrf_token()))

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
    return (SETTINGS_HTML
            .replace("{email}",                   he(user["email"]))
            .replace("{api_key}",                 user["api_key"])
            .replace("{ntfy_topic}",              topic)
            .replace("{push_checked}",            "checked" if user["notify_push"]    else "")
            .replace("{ntfy_checked}",            "checked" if user["notify_ntfy"]    else "")
            .replace("{email_checked}",           "checked" if user["notify_email"]   else "")
            .replace("{minimal_logging_checked}", "checked" if user["minimal_logging"] else "")
            .replace("{postmark_warn}",           postmark_warn)
            .replace("{postmark_js}",             "true" if postmark_ok else "false")
            .replace("{csrf_token}",              get_csrf_token()))

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
    ("amex",         "American Express",      "💳", "#e8f0fe", "Banking & Finance"),
    ("chase",        "Chase",                 "🏦", "#e3f2fd", "Banking & Finance"),
    ("sfcu",         "Stanford FCU",          "🏦", "#dbeafe", "Banking & Finance"),
    ("amazon",       "Amazon",                "📦", "#fff8e1", "Shopping"),
    ("delta",        "Delta",                 "✈️", "#e3f2fd", "Travel"),
    ("hertz",        "Hertz",                 "🚗", "#fff3e0", "Travel"),
    ("marriott",     "Marriott Bonvoy",       "🏨", "#fce8e6", "Travel"),
    ("hilton",       "Hilton Honors",         "🏨", "#e8f5e9", "Travel"),
    ("disney_plus",  "Disney+",               "🎬", "#e8f0fe", "Entertainment"),
    ("ticketmaster", "Ticketmaster",          "🎟️", "#fce8e6", "Entertainment"),
    ("xfinity",      "Xfinity",              "📡", "#e8f5e9", "Utilities & Bills"),
    ("pa_utilities", "Palo Alto Utilities",   "⚡", "#fff3e0", "Utilities & Bills"),
    ("pamf",         "PAMF MyChart",          "🏥", "#e8f5e9", "Health"),
]


def _field_config_html(source: str, configured: set, extra_data: dict = None) -> str:
    """Render AI-discovered field checkboxes, or a Discover button if not yet run."""
    if source not in configured:
        return ""
    extra      = extra_data or {}
    discovered = extra.get("discovered_fields", [])
    enabled    = set(extra.get("enabled_fields", []))
    src        = he(source)

    if discovered:
        checkboxes = ""
        for f in discovered:
            fkey   = he(f.get("key", ""))
            flbl   = he(f.get("label", ""))
            fval   = he(f.get("value", ""))
            chkd   = "checked" if f.get("key") in enabled else ""
            checkboxes += (
                f'<label style="display:flex;align-items:center;gap:8px;'
                f'font-size:12px;color:#374151;padding:5px 0;cursor:pointer;'
                f'border-bottom:1px solid #f9f7f5">'
                f'<input type="checkbox" id="field-{src}-{fkey}" '
                f'data-source="{src}" data-key="{fkey}" {chkd} '
                f'style="width:14px;height:14px;cursor:pointer;flex-shrink:0">'
                f'<span style="flex:1">{flbl}</span>'
                f'<span style="color:#9ca3af;font-size:11px">{fval}</span></label>'
            )
        return (
            f'<details class="field-config" id="fields-{src}" open>'
            f'<summary style="font-size:12px;font-weight:600;color:#7c3aed;'
            f'cursor:pointer;user-select:none;padding:8px 0 4px;display:flex;'
            f'align-items:center;gap:6px">✦ Data fields</summary>'
            f'<div style="padding:2px 0 4px;border-top:1px solid #f0ede8;margin-top:4px">'
            f'{checkboxes}'
            f'<div style="display:flex;gap:8px;margin-top:10px">'
            f'<button class="btn-save" style="font-size:12px;padding:6px 14px" '
            f'onclick="saveFields(\'{src}\')">Save</button>'
            f'<button style="font-size:12px;padding:6px 12px;border-radius:7px;'
            f'border:1px solid #e5e3df;background:#fff;cursor:pointer;color:#6b7280" '
            f'onclick="discoverFields(\'{src}\')">Re-discover ↺</button>'
            f'<button style="font-size:12px;padding:6px 12px;border-radius:7px;'
            f'border:1px solid #fecaca;background:#fff;cursor:pointer;color:#ef4444" '
            f'onclick="resetFields(\'{src}\')">Reset ✕</button>'
            f'</div></div></details>'
        )
    else:
        return (
            f'<div id="discover-area-{src}" style="margin-top:8px">'
            f'<button id="discover-btn-{src}" '
            f'style="font-size:12px;padding:7px 14px;border-radius:8px;'
            f'background:#f3f0ff;border:1px solid #d4c6ff;color:#7c3aed;'
            f'cursor:pointer;font-family:inherit;font-weight:600" '
            f'onclick="discoverFields(\'{src}\')">'
            f'✦ Discover data fields</button>'
            f'<div id="discover-result-{src}"></div>'
            f'</div>'
        )


def _build_credentials_page(user, configured: set, extra_by_source: dict = None) -> str:
    extra_by_source = extra_by_source or {}
    """Generate the credentials management page HTML."""
    csrf = get_csrf_token()

    # Group sites by category
    categories: dict = {}
    for key, name, icon, color, cat in SUPPORTED_SITES:
        categories.setdefault(cat, []).append((key, name, icon, color))

    sections_html = ""
    for cat, sites in categories.items():
        cards = ""
        for key, name, icon, color in sites:
            is_set = key in configured
            badge = (
                '<span style="font-size:10px;font-weight:600;padding:2px 8px;'
                'border-radius:99px;background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0">Connected</span>'
                if is_set else
                '<span style="font-size:10px;color:#9ca3af">Not connected</span>'
            )
            cards += f"""
<div class="cred-card" id="card-{he(key)}">
  <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
    <div style="width:32px;height:32px;border-radius:8px;background:{he(color)};display:flex;align-items:center;justify-content:center;font-size:15px">{icon}</div>
    <div style="flex:1">
      <div style="font-size:13px;font-weight:600;color:#1a1a1a">{he(name)}</div>
      <div id="badge-{he(key)}">{badge}</div>
    </div>
    <button class="btn-toggle" onclick="toggleForm('{he(key)}')" id="btn-{he(key)}">
      {'Edit' if is_set else 'Connect'}
    </button>
    {f'<button class="btn-remove" onclick="removeCred(\'{he(key)}\',\'{he(name)}\')">Remove</button>' if is_set else ''}
  </div>
  <div class="cred-form" id="form-{he(key)}" style="display:none">
    <input type="text" name="username" placeholder="Username or email"
           autocomplete="off" id="u-{he(key)}">
    <input type="password" name="password" placeholder="Password"
           autocomplete="new-password" id="p-{he(key)}">
    <details style="margin-top:8px">
      <summary style="font-size:12px;color:#6b7280;cursor:pointer;user-select:none">
        Authenticator app 2FA (optional)
      </summary>
      <input type="text" name="totp" placeholder="TOTP secret key"
             style="margin-top:6px" id="t-{he(key)}">
      <div style="font-size:11px;color:#9ca3af;margin-top:4px">
        Disable &amp; re-enable 2FA on the site, choose "Enter key manually", paste the string here.
      </div>
    </details>
    <button class="btn-save" onclick="saveCred('{he(key)}')">Save</button>
  </div>
  {_field_config_html(key, configured, extra_by_source.get(key, {}))}
</div>"""
        sections_html += f"""
<div style="margin-bottom:28px">
  <div style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
              color:#9ca3af;margin-bottom:12px">{he(cat)}</div>
  {cards}
</div>"""

    email_section = f"""
<div style="margin-bottom:28px">
  <div style="font-size:11px;font-weight:600;letter-spacing:.05em;text-transform:uppercase;
              color:#9ca3af;margin-bottom:12px">Email verification codes (auto-fill)</div>
  <div class="cred-card">
    <p style="font-size:13px;color:#6b7280;margin-bottom:12px;line-height:1.5">
      Many sites send a one-time code to your email. Provide a Gmail address and
      <a href="https://myaccount.google.com/apppasswords" target="_blank" style="color:#7c3aed">App Password</a>
      and the scraper will fetch and fill these codes automatically.
    </p>
    <input type="email" id="email-addr" placeholder="Gmail address"
           value="" autocomplete="off">
    <input type="password" id="email-pw" placeholder="Gmail App Password (16 chars)"
           autocomplete="new-password">
    <button class="btn-save" onclick="saveEmail()">Save email config</button>
  </div>
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Connected Accounts — Mighty</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
     background:#f8f7f5;color:#1a1a1a;min-height:100vh}}
.topbar{{height:56px;border-bottom:1px solid #e5e3df;background:#fff;
         display:flex;align-items:center;justify-content:space-between;padding:0 24px;position:sticky;top:0;z-index:10}}
.topbar-logo{{display:flex;align-items:center;gap:8px;text-decoration:none}}
.logo-mark{{width:28px;height:28px;border-radius:7px;overflow:hidden}}
.logo-mark img{{width:100%;height:100%;object-fit:cover}}
.logo-name{{font-size:15px;font-weight:800;letter-spacing:.4px;
            background:linear-gradient(135deg,#7c3aed,#6d28d9);
            -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text}}
.topbar-right{{display:flex;align-items:center;gap:16px}}
.topbar-link{{font-size:13px;color:#6b7280;text-decoration:none}}
.page{{max-width:680px;margin:0 auto;padding:32px 24px}}
h1{{font-size:20px;font-weight:700;margin-bottom:6px}}
.sub{{font-size:13px;color:#6b7280;margin-bottom:28px;line-height:1.5}}
.cred-card{{background:#fff;border:1px solid #e5e3df;border-radius:12px;padding:16px 18px;margin-bottom:10px}}
.cred-form input{{width:100%;padding:8px 12px;border:1.5px solid #e5e3df;border-radius:8px;
                  font-size:13px;font-family:inherit;outline:none;margin-top:8px;
                  transition:border-color 0.12s;background:#fff;color:#1a1a1a}}
.cred-form input:focus{{border-color:#7c3aed}}
.btn-toggle{{padding:5px 12px;border-radius:7px;border:1px solid #e5e3df;
             background:#fff;font-size:12px;font-weight:600;color:#7c3aed;cursor:pointer;font-family:inherit}}
.btn-toggle:hover{{background:#f3f0ff;border-color:#d4c6ff}}
.btn-remove{{padding:5px 10px;border-radius:7px;border:1px solid #fecaca;
             background:#fff;font-size:12px;color:#ef4444;cursor:pointer;font-family:inherit}}
.btn-remove:hover{{background:#fff0f0}}
.btn-save{{margin-top:12px;padding:9px 18px;border-radius:8px;background:#7c3aed;
           color:#fff;border:none;font-size:13px;font-weight:600;cursor:pointer;font-family:inherit}}
.btn-save:hover{{background:#6d28d9}}
.toast{{position:fixed;bottom:24px;right:24px;background:#1a1a1a;color:#fff;
        padding:10px 18px;border-radius:9px;font-size:13px;opacity:0;
        transition:opacity 0.2s;pointer-events:none;z-index:100}}
.toast.show{{opacity:1}}
</style>
</head>
<body>
<div class="topbar">
  <a class="topbar-logo" href="/dashboard">
    <div class="logo-mark"><img src="/logo-icon.png" alt="Mighty"></div>
    <div class="logo-name">Mighty</div>
  </a>
  <div class="topbar-right">
    <a class="topbar-link" href="/dashboard">Dashboard</a>
    <a class="topbar-link" href="/settings">Settings</a>
    <span style="font-size:13px;color:#9ca3af">{he(user["email"])}</span>
  </div>
</div>

<div class="page">
  <h1>Connected accounts</h1>
  <p class="sub">
    Add credentials for each site you want the scraper to monitor.
    Credentials are encrypted and stored securely — only you can access them via your API key.
  </p>

  {sections_html}
  {email_section}
</div>

<div class="toast" id="toast"></div>

<script>
var CSRF = '{csrf}';

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
  fetch('/credentials/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF, source: key, username: u, password: p, totp_secret: t}})
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{
      toast('Saved ✓');
      document.getElementById('badge-' + key).innerHTML =
        '<span style="font-size:10px;font-weight:600;padding:2px 8px;border-radius:99px;background:#f0fdf4;color:#15803d;border:1px solid #bbf7d0">Connected</span>';
      document.getElementById('btn-' + key).textContent = 'Edit';
      document.getElementById('form-' + key).style.display = 'none';
      location.reload();
    }} else {{ toast(d.error || 'Error', false); }}
  }});
}}

function saveEmail() {{
  var addr = document.getElementById('email-addr').value.trim();
  var pw   = document.getElementById('email-pw').value;
  if (!addr || !pw) {{ toast('Email address and app password required', false); return; }}
  fetch('/credentials/save', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF, source: '_email', username: addr, password: pw}})
  }}).then(r => r.json()).then(d => {{
    if (d.ok) {{ toast('Email config saved ✓'); }}
    else {{ toast(d.error || 'Error', false); }}
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
  if (!confirm('Clear all discovered fields for this account and re-discover fresh?')) return;
  fetch('/credentials/fields/reset/' + source, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF}})
  }}).then(function(r) {{ return r.json(); }}).then(function(d) {{
    if (d.ok) discoverFields(source);
    else toast(d.error || 'Reset failed', false);
  }}).catch(function(e) {{
    toast('Reset failed — try again', false);
  }});
}}

function discoverFields(source) {{
  var btn = document.getElementById('discover-btn-' + source);
  if (btn) {{ btn.textContent = 'Discovering...'; btn.disabled = true; }}
  fetch('/credentials/discover/' + source, {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF}})
  }}).then(function(r) {{ return r.json(); }}).then(function(data) {{
    if (data.ok) {{
      toast(data.fields.length + ' fields found');
      setTimeout(function() {{ location.reload(); }}, 1200);
    }} else {{
      toast(data.error || 'Discovery failed', false);
      if (btn) {{ btn.textContent = 'Discover data fields'; btn.disabled = false; }}
    }}
  }}).catch(function(e) {{
    toast('Discovery failed — try syncing again first', false);
    if (btn) {{ btn.textContent = 'Discover data fields'; btn.disabled = false; }}
  }});
}}

function saveFields(source) {{
  var boxes = document.querySelectorAll('[data-source="' + source + '"]');
  var enabled = Array.from(boxes).filter(b => b.checked).map(b => b.dataset.key);
  fetch('/credentials/fields', {{
    method: 'POST',
    headers: {{'Content-Type': 'application/x-www-form-urlencoded'}},
    body: new URLSearchParams({{_csrf: CSRF, source: source, enabled_fields: JSON.stringify(enabled)}})
  }}).then(r => r.json()).then(d => {{ if (d.ok) toast('Saved ✓'); }});
}}

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
    return _build_credentials_page(user, configured, extra_by_source)


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
    new_enc = encrypt_cred(uid, json.dumps(extra)) if extra else ""
    db.execute(
        "UPDATE account_credentials SET extra_enc=?, updated_at=? WHERE user_id=? AND source=?",
        (new_enc, iso(), uid, source)
    )
    db.commit()
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
    try:
        fields = claude_discover_fields(raw_text, site_name)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Discovery error: {str(e)[:100]}"}), 500
    if not fields:
        return jsonify({"ok": False, "error": "Could not identify fields — try syncing again"}), 500

    # Merge with existing — never remove previously discovered fields
    cred_row = get_db().execute(
        "SELECT extra_enc FROM account_credentials WHERE user_id=? AND source=?",
        (uid, source)
    ).fetchone()
    ex = {}
    if cred_row and cred_row["extra_enc"]:
        try: ex = json.loads(decrypt_cred(uid, cred_row["extra_enc"]))
        except Exception: pass

    existing  = ex.get("discovered_fields", [])
    ex_by_key = {f["key"]: f for f in existing}
    ex_enabled = set(ex.get("enabled_fields", []))

    for f in fields:
        key = f["key"]
        if key in ex_by_key:
            ex_by_key[key]["value"] = f.get("value", "")  # refresh value
        else:
            ex_by_key[key] = f          # new field — add it
            ex_enabled.add(key)         # auto-enable new fields

    # Deduplicate: keep first-seen field when value OR normalized label matches
    def _norm(s: str) -> str:
        return re.sub(r'[^a-z0-9]', '', s.lower())

    seen_values: dict = {}
    seen_labels: set  = set()
    deduped = []
    for f in ex_by_key.values():
        val   = str(f.get("value", "")).strip()
        label = _norm(f.get("label", ""))

        # Check label similarity — skip if an existing label contains this one or vice versa
        label_dup = any(label in sl or sl in label for sl in seen_labels)

        # Check value duplicate (non-trivial values only)
        value_dup = (val and val not in ("0", "") and val in seen_values)

        if label_dup or value_dup:
            ex_enabled.discard(f["key"])
            continue

        seen_labels.add(label)
        if val and val not in ("0", ""):
            seen_values[val] = f["key"]
        deduped.append(f)

    merged_fields   = deduped
    merged_enabled  = list(ex_enabled | {f["key"] for f in fields})  # never lose enabled

    ex["enabled_fields"]    = merged_enabled
    ex["discovered_fields"] = merged_fields
    get_db().execute(
        "UPDATE account_credentials SET extra_enc=?, updated_at=? WHERE user_id=? AND source=?",
        (encrypt_cred(uid, json.dumps(ex)), iso(), uid, source)
    )
    get_db().commit()
    return jsonify({"ok": True, "fields": fields})


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
    # raw_text is sent inside data{} by the scraper — it's already in data dict

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

    if not has_prefs and raw_text and _claude:
        # Discover fields in the background
        import threading
        site_name = display
        uid = user["id"]
        def _bg_discover():
            fields = claude_discover_fields(raw_text, site_name)
            if fields:
                enabled = [f["key"] for f in fields]
                ex2 = {}
                if cred_row and cred_row["extra_enc"]:
                    try: ex2 = json.loads(decrypt_cred(uid, cred_row["extra_enc"]))
                    except Exception: pass
                ex2["enabled_fields"]   = enabled
                ex2["discovered_fields"] = fields
                new_enc = encrypt_cred(uid, json.dumps(ex2))
                with app.app_context():
                    get_db().execute(
                        "UPDATE account_credentials SET extra_enc=?, updated_at=? "
                        "WHERE user_id=? AND source=?",
                        (new_enc, iso(), uid, source)
                    )
                    get_db().commit()
        threading.Thread(target=_bg_discover, daemon=True).start()

    return jsonify({"ok": True, "source": source})


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
    app.run(host="0.0.0.0", port=PORT, debug=False)
