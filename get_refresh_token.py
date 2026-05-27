import base64, hashlib, http.server, json, secrets, urllib.parse, urllib.request, webbrowser

ISSUER        = "https://YOUR_IDP_HOST"           # 본인 OIDC issuer (Pocket ID 등)
CLIENT_ID     = "YOUR_CLIENT_ID"                  # IDP에서 발급받은 oidc_client_id
CLIENT_SECRET = ""                                # public (PKCE) client면 ""
REDIRECT_URI  = "http://127.0.0.1:8765/callback"
SCOPES        = "openid offline_access profile email"

cfg = json.load(urllib.request.urlopen(f"{ISSUER}/.well-known/openid-configuration"))
auth_ep, token_ep = cfg["authorization_endpoint"], cfg["token_endpoint"]

verifier  = secrets.token_urlsafe(64)
challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
state     = secrets.token_urlsafe(16)

url = f"{auth_ep}?" + urllib.parse.urlencode({
    "response_type": "code", "client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI,
    "scope": SCOPES, "state": state,
    "code_challenge": challenge, "code_challenge_method": "S256",
})
print("Open:", url)
webbrowser.open(url)

code_holder = {}
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        code_holder.update(code=q.get("code", [None])[0], state=q.get("state", [None])[0])
        self.send_response(200); self.end_headers()
        self.wfile.write(b"OK, you can close this tab.")
    def log_message(self, *a): pass

http.server.HTTPServer(("127.0.0.1", 8765), H).handle_request()
assert code_holder["state"] == state, "state mismatch"

data = {
    "grant_type": "authorization_code",
    "code": code_holder["code"],
    "redirect_uri": REDIRECT_URI,
    "client_id": CLIENT_ID,
    "code_verifier": verifier,
}
if CLIENT_SECRET:
    data["client_secret"] = CLIENT_SECRET

req = urllib.request.Request(
    token_ep,
    data=urllib.parse.urlencode(data).encode(),
    headers={"Content-Type": "application/x-www-form-urlencoded"},
)
tok = json.load(urllib.request.urlopen(req))
if "refresh_token" not in tok:
    raise SystemExit(f"No refresh_token in response — check offline_access scope.\nResponse: {tok}")
print("\n=== refresh_token ===\n" + tok["refresh_token"])
