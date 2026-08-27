def split_host_port(host: str) -> tuple[str, str]:
    raw = str(host or "").strip()
    if not raw:
        return "", ""
    if raw.startswith("["):
        end = raw.find("]")
        if end < 0:
            return raw, ""
        name = raw[: end + 1]
        rest = raw[end + 1 :]
        return name, rest[1:] if rest.startswith(":") else ""
    if raw.count(":") == 1:
        name, port = raw.rsplit(":", 1)
        return name, port
    return raw, ""


def public_origin(proto: str, host: str, forwarded_port: str = "") -> str:
    proto = str(proto or "http").split(",")[0].strip().lower()
    if proto not in {"http", "https"}:
        proto = "http"
    host = str(host or "").split(",")[0].strip()
    name, port = split_host_port(host)
    if not port:
        port = str(forwarded_port or "").split(",")[0].strip()
    if port.isdigit():
        n = int(port)
        if n <= 0 or n > 65535:
            port = ""
    elif port:
        port = ""
    if not name:
        return ""
    default = "443" if proto == "https" else "80"
    if port and port != default:
        return f"{proto}://{name}:{port}"
    return f"{proto}://{name}"
