from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import json, secrets, os, time

HOST = "127.0.0.1"
PORT = int(os.environ.get("KOSFLOW_BRIDGE_PORT", "8788"))
OLLAMA = os.environ.get("OLLAMA_HOST_URL", "http://127.0.0.1:11434")
PAIR_TOKEN = os.environ.get("KOSFLOW_PAIR_TOKEN") or secrets.token_urlsafe(8)

fails = {}

def client_key(handler):
    return handler.client_address[0] if handler.client_address else "unknown"

def allowed(handler):
    token = handler.headers.get("X-KosFlow-Token", "")
    key = client_key(handler)
    rec = fails.get(key, {"count":0, "until":0})
    if time.time() < rec["until"]:
        return False
    if secrets.compare_digest(token, PAIR_TOKEN):
        fails.pop(key, None)
        return True
    rec["count"] += 1
    if rec["count"] >= 8:
        rec["until"] = time.time() + 60
        rec["count"] = 0
    fails[key] = rec
    return False

def ollama_raw(path, method="GET", payload=None, timeout=300):
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = Request(
        OLLAMA + path,
        data=body,
        method=method,
        headers={"Content-Type":"application/json"},
    )
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "ignore")
            return r.status, raw
    except HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        return e.code, raw

def ollama_json(path, method="GET", payload=None, timeout=300):
    status, raw = ollama_raw(path, method, payload, timeout)
    try:
        data = json.loads(raw) if raw else {}
    except Exception:
        data = {"raw":raw}
    return status, data

class H(BaseHTTPRequestHandler):
    server_version = "KosFlowLocalBridge/10.7"

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-KosFlow-Token")
        self.send_header("Access-Control-Max-Age", "600")
        if self.headers.get("Access-Control-Request-Private-Network", "").lower() == "true":
            self.send_header("Access-Control-Allow-Private-Network", "true")

    def reply(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self.cors()
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            ok = False
            detail = None
            try:
                status, data = ollama_json("/api/tags", timeout=4)
                ok = status == 200
                if not ok:
                    detail = data
            except Exception as e:
                detail = str(e)
            return self.reply({"bridge":"ok", "ollama":ok, "port":PORT, "detail":detail})

        if self.path == "/models":
            if not allowed(self):
                return self.reply({"error":"pairing token invalid"}, 401)
            try:
                status, data = ollama_json("/api/tags", timeout=6)
                if status != 200:
                    return self.reply({"error":"Ollama tags failed", "ollama_error":data}, 502)
                return self.reply({
                    "models":[x.get("name") for x in data.get("models",[]) if x.get("name")]
                })
            except Exception as e:
                return self.reply({"error":"Tidak bisa membaca model Ollama", "detail":str(e)}, 500)

        return self.reply({"error":"not found"}, 404)

    def do_POST(self):
        if not allowed(self):
            return self.reply({"error":"pairing token invalid"}, 401)

        n = int(self.headers.get("Content-Length", "0"))
        try:
            p = json.loads(self.rfile.read(n).decode("utf-8")) if n else {}
        except Exception:
            return self.reply({"error":"invalid json"}, 400)

        if self.path == "/generate":
            model = p.get("model")
            prompt = p.get("prompt", "")
            if not model:
                return self.reply({"error":"Model Ollama belum dipilih"}, 400)
            if not prompt:
                return self.reply({"error":"Prompt kosong"}, 400)

            # Keep local prompt reasonably bounded to avoid context/memory errors.
            if len(prompt) > 24000:
                prompt = prompt[:24000]

            req = {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "keep_alive": "5m",
                "options": {
                    "temperature": float(p.get("temperature", 0.10)),
                    "num_predict": 1200,
                },
            }

            try:
                status, data = ollama_json("/api/generate", "POST", req, timeout=360)

                # Some models/builds may reject format=json. Retry once without it.
                if status >= 400:
                    first_error = data
                    req.pop("format", None)
                    status, data = ollama_json("/api/generate", "POST", req, timeout=360)
                    if status >= 400:
                        return self.reply({
                            "error":"Ollama generation failed",
                            "ollama_error":data,
                            "first_attempt":first_error,
                            "model":model,
                        }, 502)

                return self.reply({
                    "response":data.get("response", ""),
                    "model":model,
                    "done":data.get("done"),
                    "eval_count":data.get("eval_count"),
                    "total_duration":data.get("total_duration"),
                })
            except Exception as e:
                return self.reply({
                    "error":"Bridge gagal memanggil Ollama",
                    "detail":str(e),
                    "model":model,
                }, 500)

        return self.reply({"error":"not found"}, 404)

def main():
    print("="*62)
    print(" KosFlow Finance - Local Ollama Bridge v10.7")
    print("="*62)
    print(f"Bridge        : http://{HOST}:{PORT}")
    print(f"Ollama        : {OLLAMA}")
    print(f"PAIRING TOKEN : {PAIR_TOKEN}")
    print()
    print("Bridge sekarang menampilkan detail error Ollama bila generate gagal.")
    print("="*62)
    ThreadingHTTPServer((HOST, PORT), H).serve_forever()

if __name__ == "__main__":
    main()
