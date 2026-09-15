
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import json, secrets, os, time

HOST="127.0.0.1"
PORT=int(os.environ.get("KOSFLOW_BRIDGE_PORT","8788"))
OLLAMA=os.environ.get("OLLAMA_HOST_URL","http://127.0.0.1:11434")
PAIR_TOKEN=os.environ.get("KOSFLOW_PAIR_TOKEN") or secrets.token_urlsafe(8)

# Simple in-memory brute-force throttle.
fails={}
def client_key(handler):
    return handler.client_address[0] if handler.client_address else "unknown"

def allowed(handler):
    token=handler.headers.get("X-KosFlow-Token","")
    key=client_key(handler)
    rec=fails.get(key,{"count":0,"until":0})
    if time.time()<rec["until"]:
        return False
    if secrets.compare_digest(token,PAIR_TOKEN):
        fails.pop(key,None)
        return True
    rec["count"]+=1
    if rec["count"]>=8:
        rec["until"]=time.time()+60
        rec["count"]=0
    fails[key]=rec
    return False

def ollama_json(path, method="GET", payload=None, timeout=240):
    body=json.dumps(payload).encode() if payload is not None else None
    req=Request(OLLAMA+path,data=body,method=method,headers={"Content-Type":"application/json"})
    with urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8","ignore"))

class H(BaseHTTPRequestHandler):
    server_version="KosFlowLocalBridge/9.0"

    def cors(self):
        # No cookies/credentials are used; pairing token protects privileged endpoints.
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers","Content-Type, X-KosFlow-Token")
        self.send_header("Access-Control-Max-Age","600")
        if self.headers.get("Access-Control-Request-Private-Network","").lower()=="true":
            self.send_header("Access-Control-Allow-Private-Network","true")

    def reply(self,obj,status=200):
        data=json.dumps(obj,ensure_ascii=False).encode()
        self.send_response(status);self.cors()
        self.send_header("Content-Type","application/json; charset=utf-8")
        self.send_header("Content-Length",str(len(data)));self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204);self.cors();self.end_headers()

    def do_GET(self):
        if self.path=="/health":
            ok=False
            try:
                ollama_json("/api/tags",timeout=3);ok=True
            except Exception:pass
            return self.reply({"bridge":"ok","ollama":ok,"port":PORT})
        if self.path=="/models":
            if not allowed(self):return self.reply({"error":"pairing token invalid"},401)
            try:
                d=ollama_json("/api/tags",timeout=5)
                return self.reply({"models":[x.get("name") for x in d.get("models",[]) if x.get("name")]})
            except Exception as e:return self.reply({"error":str(e)},500)
        return self.reply({"error":"not found"},404)

    def do_POST(self):
        if not allowed(self):return self.reply({"error":"pairing token invalid"},401)
        n=int(self.headers.get("Content-Length","0"))
        try:p=json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except:return self.reply({"error":"invalid json"},400)
        if self.path=="/generate":
            try:
                req={
                    "model":p.get("model"),
                    "prompt":p.get("prompt",""),
                    "stream":False,
                    "options":{"temperature":float(p.get("temperature",0.15))}
                }
                d=ollama_json("/api/generate","POST",req,timeout=300)
                return self.reply({"response":d.get("response",""),"model":p.get("model")})
            except Exception as e:return self.reply({"error":str(e)},500)
        return self.reply({"error":"not found"},404)

def main():
    print("="*58)
    print(" KosFlow AI Local Ollama Bridge v9")
    print("="*58)
    print(f"Bridge : http://{HOST}:{PORT}")
    print(f"Ollama : {OLLAMA}")
    print(f"PAIRING TOKEN: {PAIR_TOKEN}")
    print()
    print("Masukkan token di web KosFlow AI.")
    print("Bridge menangani CORS + Private Network Access preflight.")
    print("Jangan membagikan token selama bridge aktif.")
    print("="*58)
    ThreadingHTTPServer((HOST,PORT),H).serve_forever()

if __name__=="__main__":
    main()
