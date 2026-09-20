from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json, os, secrets, socket, sys, time, traceback

HOST = os.getenv("KOSFLOW_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("KOSFLOW_BRIDGE_PORT", "8788"))
OLLAMA = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
TOKEN = os.getenv("KOSFLOW_PAIRING_TOKEN") or secrets.token_urlsafe(8)[:11]

# Streaming keeps data flowing from Ollama so long generations do not sit silent.
CONNECT_TIMEOUT = int(os.getenv("KOSFLOW_OLLAMA_CONNECT_TIMEOUT", "30"))
READ_TIMEOUT = int(os.getenv("KOSFLOW_OLLAMA_READ_TIMEOUT", "900"))
DEFAULT_NUM_CTX = int(os.getenv("KOSFLOW_NUM_CTX", "4096"))
DEFAULT_NUM_PREDICT = int(os.getenv("KOSFLOW_NUM_PREDICT", "1000"))
KEEP_ALIVE = os.getenv("KOSFLOW_KEEP_ALIVE", "10m")

DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)

def request_json(url, payload=None, method=None, timeout=30):
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

def stream_generate(payload):
    """
    Internal streaming from Ollama.
    The browser still receives one final JSON response, but the bridge continuously
    consumes Ollama chunks instead of waiting silently for one huge response.
    """
    body=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    req=Request(
        OLLAMA+"/api/generate",
        data=body,
        headers={"Content-Type":"application/json"},
        method="POST",
    )

    chunks=[]
    final={}
    with urlopen(req, timeout=READ_TIMEOUT) as r:
        while True:
            line=r.readline()
            if not line:
                break
            line=line.strip()
            if not line:
                continue
            obj=json.loads(line.decode("utf-8",errors="replace"))
            piece=obj.get("response","")
            if piece:
                chunks.append(piece)
            final=obj
            if obj.get("done") is True:
                break

    final_response="".join(chunks)
    return final_response, final

class Handler(BaseHTTPRequestHandler):
    server_version="KosFlowBridge/10.39"
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
            print(f"CLIENT DISCONNECTED: {type(e).__name__}: {e}")
            return False
        except OSError as e:
            if getattr(e,"winerror",None) in (10053,10054):
                print(f"CLIENT DISCONNECTED: WinError {e.winerror}; no secondary 500 response.")
                return False
            raise

    def token_ok(self):
        return self.headers.get("X-KosFlow-Token","")==TOKEN

    def do_OPTIONS(self):
        try:
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length","0")
            self.end_headers()
        except Exception:
            pass

    def do_GET(self):
        if self.path=="/health":
            return self.safe_reply({
                "ok":True,
                "bridge":"KosFlow Local Ollama Bridge",
                "version":"10.39",
                "ollama":OLLAMA,
                "streaming_internal":True,
                "read_timeout_seconds":READ_TIMEOUT,
                "default_num_ctx":DEFAULT_NUM_CTX,
                "default_num_predict":DEFAULT_NUM_PREDICT,
                "keep_alive":KEEP_ALIVE
            })
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
        temperature=float(req.get("temperature",0.1))
        num_ctx=int(req.get("num_ctx") or DEFAULT_NUM_CTX)
        num_predict=int(req.get("num_predict") or DEFAULT_NUM_PREDICT)

        if not model or not prompt:
            return self.safe_reply({"error":"model dan prompt wajib diisi"},400)

        payload={
            "model":model,
            "prompt":prompt,
            "stream":True,
            "keep_alive":KEEP_ALIVE,
            "options":{
                "temperature":temperature,
                "num_ctx":num_ctx,
                "num_predict":num_predict,
            },
        }
        if schema:
            payload["format"]=schema

        started=time.time()
        print(
            f"GENERATE START: model={model} | stream=true | "
            f"num_ctx={num_ctx} | num_predict={num_predict} | prompt_chars={len(prompt)}"
        )
        sys.stdout.flush()

        try:
            response_text,meta=stream_generate(payload)
            elapsed=round(time.time()-started,2)

            response={
                "response":response_text,
                "model":meta.get("model",model),
                "done":meta.get("done",True),
                "done_reason":meta.get("done_reason"),
                "total_duration":meta.get("total_duration"),
                "load_duration":meta.get("load_duration"),
                "prompt_eval_count":meta.get("prompt_eval_count"),
                "eval_count":meta.get("eval_count"),
                "eval_duration":meta.get("eval_duration"),
                "elapsed_seconds":elapsed,
                "streaming_internal":True,
                "num_predict":num_predict,
            }

            print(
                f"GENERATE DONE: model={model} | {elapsed}s | "
                f"chars={len(response_text)} | eval_count={response.get('eval_count')} | "
                f"done_reason={response.get('done_reason')}"
            )
            sys.stdout.flush()
            self.safe_reply(response,200)
            return

        except HTTPError as e:
            elapsed=round(time.time()-started,2)
            raw=e.read().decode("utf-8",errors="replace")
            print(f"OLLAMA HTTP ERROR: {e.code} | {elapsed}s | {raw[:500]}")
            return self.safe_reply({
                "error":"Ollama generate gagal",
                "detail":raw or str(e),
                "model":model,
                "elapsed_seconds":elapsed,
            },502)

        except (socket.timeout, TimeoutError) as e:
            elapsed=round(time.time()-started,2)
            print(f"OLLAMA STREAM TIMEOUT: {type(e).__name__} | {elapsed}s | {e}")
            return self.safe_reply({
                "error":"Ollama belum selesai sebelum batas waktu stream",
                "detail":str(e),
                "model":model,
                "timeout_seconds":READ_TIMEOUT,
            },504)

        except URLError as e:
            elapsed=round(time.time()-started,2)
            print(f"OLLAMA CONNECTION ERROR: {elapsed}s | {e}")
            return self.safe_reply({
                "error":"Bridge gagal terhubung ke Ollama",
                "detail":str(e),
                "model":model,
            },502)

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
            return self.safe_reply({
                "error":"Bridge gagal memanggil Ollama",
                "detail":str(e),
                "model":model,
            },500)

        except Exception as e:
            elapsed=round(time.time()-started,2)
            print(f"BRIDGE ERROR: {type(e).__name__} | {elapsed}s | {e}")
            traceback.print_exc()
            return self.safe_reply({
                "error":"Bridge gagal memanggil Ollama",
                "detail":str(e),
                "model":model,
            },500)

if __name__=="__main__":
    print("Memeriksa Ollama...")
    print("="*62)
    print(" KosFlow Finance - Local Ollama Bridge v10.39")
    print("="*62)
    print(f"Bridge        : http://{HOST}:{PORT}")
    print(f"Ollama        : {OLLAMA}")
    print(f"PAIRING TOKEN : {TOKEN}")
    print(f"Internal stream: ON")
    print(f"Read timeout   : {READ_TIMEOUT} detik")
    print(f"Default ctx    : {DEFAULT_NUM_CTX}")
    print(f"Default predict: {DEFAULT_NUM_PREDICT}")
    print(f"Keep alive     : {KEEP_ALIVE}")
    print()
    print("Bridge sekarang membaca stream token dari Ollama secara langsung.")
    print("Output final tetap dikirim ke web sebagai satu JSON setelah selesai.")
    print("="*62)

    httpd=ThreadingHTTPServer((HOST,PORT),Handler)
    httpd.daemon_threads=True
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
