from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json, os, secrets, socket, sys, time, traceback

HOST = os.getenv("KOSFLOW_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("KOSFLOW_BRIDGE_PORT", "8788"))
OLLAMA = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
TOKEN = os.getenv("KOSFLOW_PAIRING_TOKEN") or secrets.token_urlsafe(8)[:11]
OLLAMA_TIMEOUT = int(os.getenv("KOSFLOW_OLLAMA_TIMEOUT", "600"))

DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, TimeoutError)

def request_json(url, payload=None, method=None, timeout=OLLAMA_TIMEOUT):
    body=None
    headers={"Content-Type":"application/json"}
    if payload is not None:
        body=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    req=Request(url,data=body,headers=headers,method=method or ("POST" if body else "GET"))
    try:
        with urlopen(req,timeout=timeout) as r:
            raw=r.read()
            return r.status,json.loads(raw.decode("utf-8",errors="replace") or "{}")
    except HTTPError as e:
        raw=e.read().decode("utf-8",errors="replace")
        try: data=json.loads(raw)
        except Exception: data={"error":raw or str(e)}
        return e.code,data

class Handler(BaseHTTPRequestHandler):
    server_version="KosFlowBridge/10.38"
    protocol_version="HTTP/1.1"

    def log_message(self,fmt,*args):
        sys.stdout.write("%s - - [%s] %s\n"%(self.client_address[0],self.log_date_time_string(),fmt%args))
        sys.stdout.flush()

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin","*")
        self.send_header("Access-Control-Allow-Headers","Content-Type, X-KosFlow-Token, X-KosFlow-Client")
        self.send_header("Access-Control-Allow-Methods","GET, POST, OPTIONS")
        self.send_header("Cache-Control","no-store")
        self.send_header("Connection","close")

    def safe_reply(self,obj,status=200):
        data=json.dumps(obj,ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Content-Length",str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            self.wfile.flush()
            return True
        except DISCONNECT_ERRORS as e:
            print(f"CLIENT DISCONNECTED: browser closed before response was sent ({type(e).__name__}: {e})")
            return False
        except OSError as e:
            if getattr(e,"winerror",None) in (10053,10054):
                print(f"CLIENT DISCONNECTED: WinError {e.winerror}; response dibuang tanpa kirim 500 kedua.")
                return False
            raise

    def token_ok(self):
        return self.headers.get("X-KosFlow-Token","")==TOKEN

    def do_OPTIONS(self):
        try:
            self.send_response(204); self._cors(); self.send_header("Content-Length","0"); self.end_headers()
        except Exception:
            pass

    def do_GET(self):
        if self.path=="/health":
            return self.safe_reply({"ok":True,"bridge":"KosFlow Local Ollama Bridge","version":"10.38","ollama":OLLAMA,"ollama_timeout_seconds":OLLAMA_TIMEOUT})
        if self.path=="/models":
            if not self.token_ok():
                return self.safe_reply({"error":"Pairing token tidak valid"},401)
            try:
                status,data=request_json(OLLAMA+"/api/tags",timeout=30)
                if status>=400:
                    return self.safe_reply({"error":"Gagal membaca model Ollama","ollama_error":data},502)
                models=[]
                for item in data.get("models",[]):
                    name=item.get("name") or item.get("model")
                    if name: models.append(name)
                return self.safe_reply({"models":models})
            except Exception as e:
                return self.safe_reply({"error":"Bridge gagal membaca model Ollama","detail":str(e)},502)
        return self.safe_reply({"error":"Not found"},404)

    def do_POST(self):
        if self.path!="/generate":
            return self.safe_reply({"error":"Not found"},404)
        if not self.token_ok():
            return self.safe_reply({"error":"Pairing token tidak valid"},401)

        try:
            length=int(self.headers.get("Content-Length","0"))
            req=json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as e:
            return self.safe_reply({"error":"JSON request tidak valid","detail":str(e)},400)

        model=str(req.get("model") or "").strip()
        prompt=str(req.get("prompt") or "")
        schema=req.get("schema")
        temperature=req.get("temperature",0.1)
        if not model or not prompt:
            return self.safe_reply({"error":"model dan prompt wajib diisi"},400)

        payload={"model":model,"prompt":prompt,"stream":False,"options":{"temperature":temperature}}
        if schema: payload["format"]=schema

        started=time.time()
        print(f"GENERATE START: model={model} | timeout={OLLAMA_TIMEOUT}s | prompt_chars={len(prompt)}")
        sys.stdout.flush()

        try:
            status,data=request_json(OLLAMA+"/api/generate",payload=payload,timeout=OLLAMA_TIMEOUT)
            elapsed=round(time.time()-started,2)

            if status>=400:
                print(f"GENERATE OLLAMA ERROR: HTTP {status} | {elapsed}s")
                return self.safe_reply({"error":"Ollama generate gagal","ollama_error":data,"model":model,"elapsed_seconds":elapsed},502)

            response={
                "response":data.get("response",""),
                "model":data.get("model",model),
                "done":data.get("done",True),
                "done_reason":data.get("done_reason"),
                "total_duration":data.get("total_duration"),
                "load_duration":data.get("load_duration"),
                "prompt_eval_count":data.get("prompt_eval_count"),
                "eval_count":data.get("eval_count"),
                "elapsed_seconds":elapsed,
            }
            print(f"GENERATE DONE: model={model} | {elapsed}s | chars={len(response['response'])}")
            sys.stdout.flush()
            self.safe_reply(response,200)
            return

        except (URLError,socket.timeout) as e:
            elapsed=round(time.time()-started,2)
            print(f"OLLAMA CONNECTION/TIMEOUT ERROR: {type(e).__name__} | {elapsed}s | {e}")
            self.safe_reply({"error":"Bridge gagal terhubung ke Ollama atau melewati timeout","detail":str(e),"model":model,"timeout_seconds":OLLAMA_TIMEOUT},504)
        except DISCONNECT_ERRORS as e:
            elapsed=round(time.time()-started,2)
            print(f"CLIENT DISCONNECTED DURING GENERATE: {type(e).__name__} | {elapsed}s")
            return
        except OSError as e:
            elapsed=round(time.time()-started,2)
            if getattr(e,"winerror",None) in (10053,10054):
                print(f"CLIENT DISCONNECTED DURING GENERATE: WinError {e.winerror} | {elapsed}s")
                return
            print(f"BRIDGE OS ERROR: {type(e).__name__}: {e}")
            self.safe_reply({"error":"Bridge gagal memanggil Ollama","detail":str(e),"model":model},500)
        except Exception as e:
            elapsed=round(time.time()-started,2)
            print(f"BRIDGE ERROR: {type(e).__name__} | {elapsed}s | {e}")
            traceback.print_exc()
            self.safe_reply({"error":"Bridge gagal memanggil Ollama","detail":str(e),"model":model},500)

if __name__=="__main__":
    print("Memeriksa Ollama...")
    print("="*62)
    print(" KosFlow Finance - Local Ollama Bridge v10.38")
    print("="*62)
    print(f"Bridge        : http://{HOST}:{PORT}")
    print(f"Ollama        : {OLLAMA}")
    print(f"PAIRING TOKEN : {TOKEN}")
    print(f"Ollama timeout: {OLLAMA_TIMEOUT} detik")
    print()
    print("WinError 10053/10054 sekarang dianggap client disconnect, bukan error Ollama.")
    print("="*62)
    httpd=ThreadingHTTPServer((HOST,PORT),Handler)
    httpd.daemon_threads=True
    try: httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt: pass
    finally: httpd.server_close()
