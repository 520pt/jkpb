from __future__ import annotations

from contextlib import asynccontextmanager
from urllib.parse import quote, unquote, urljoin, urlsplit
from xml.etree import ElementTree

import httpx
from fastapi import HTTPException


def safe_directory(value: str) -> str:
    parts = [part for part in value.strip().split("/") if part]
    if any(part in {".", ".."} or chr(92) in part or any(ord(char) in range(32) for char in part) for part in parts):
        raise HTTPException(400, "保存路径不能包含上级目录、反斜线或控制字符。")
    return "/".join(parts)


def normalize_webdav_config(config: dict) -> dict[str, str]:
    result = {key: str(config.get(key) or "") for key in ("base_url", "username", "password", "directory")}
    result["base_url"] = result["base_url"].strip().rstrip("/")
    result["username"] = result["username"].strip()
    try:
        parsed = urlsplit(result["base_url"])
        valid = parsed.scheme in {"http", "https"} and bool(parsed.hostname)
        valid = valid and parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment
        valid = valid and not any(part in {".", ".."} for part in unquote(parsed.path).split("/"))
        parsed.port
    except ValueError:
        valid = False
    if not valid or any(ord(char) in range(33) for char in result["base_url"]):
        raise HTTPException(400, "请填写有效的 HTTP/HTTPS WebDAV 地址，账号密码请填入独立配置项。")
    if len(result["password"]) > 4096:
        raise HTTPException(400, "WebDAV 密码过长。")
    result["directory"] = safe_directory(result["directory"])
    return result


@asynccontextmanager
async def webdav_client(config):
    config = normalize_webdav_config(config)
    auth = httpx.BasicAuth(config["username"], config["password"]) if config["username"] or config["password"] else None
    try:
        async with httpx.AsyncClient(timeout=60, trust_env=False, follow_redirects=False, auth=auth) as client:
            yield client, config
    except httpx.HTTPError:
        raise HTTPException(502, "连接 WebDAV 失败，请检查服务器、网络或证书。") from None


def remote_url(config, path, *, directory=False):
    url = config["base_url"] + "/" + quote(safe_directory(path), safe="/")
    return url.rstrip("/") + "/" if directory else url


def check_response(response, allowed):
    if response.status_code == 412:
        raise HTTPException(409, "WebDAV 已有同名文件，未覆盖；请重新预览目录。")
    if response.status_code not in allowed:
        raise HTTPException(502, f"WebDAV 请求失败（HTTP {response.status_code}），请检查地址、账号和目录权限。")


async def read_directory(client, config, path, *, depth=1):
    path = safe_directory(path)
    response = await client.request("PROPFIND", remote_url(config, path, directory=True), headers={"Depth": str(depth)})
    if response.status_code == 404:
        raise HTTPException(404, "WebDAV 目录不存在，请重新选择实际保存位置。")
    check_response(response, {200, 207})
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError:
        raise HTTPException(502, "服务器未返回有效的 WebDAV 目录信息。") from None
    if root.tag != "{DAV:}multistatus":
        raise HTTPException(502, "服务器未返回有效的 WebDAV 目录信息。")
    base = urlsplit(config["base_url"] + "/")
    base_path = unquote(base.path).rstrip("/") + "/"
    entries = {}
    for item in root.findall("{DAV:}response"):
        href = item.findtext("{DAV:}href")
        if not href:
            continue
        resolved = urlsplit(urljoin(config["base_url"] + "/", href))
        if (resolved.scheme, resolved.netloc) != (base.scheme, base.netloc):
            continue
        if "%2f" in resolved.path.lower() or "%5c" in resolved.path.lower():
            continue
        decoded = unquote(resolved.path)
        if decoded.rstrip("/") == base_path.rstrip("/"):
            relative = ""
        elif decoded.startswith(base_path):
            relative = decoded[len(base_path):].rstrip("/")
        else:
            continue
        if safe_directory(relative) != relative:
            continue
        own = relative == path
        prefix = path + "/" if path else ""
        if not own and (not relative.startswith(prefix) or "/" in relative[len(prefix):]):
            continue
        props = []
        for propstat in item.findall("{DAV:}propstat"):
            if (propstat.findtext("{DAV:}status") or "").split()[1:2] == ["200"]:
                prop = propstat.find("{DAV:}prop")
                if prop is not None:
                    props.append(prop)
        if not props:
            if own:
                raise HTTPException(502, "无法读取所选 WebDAV 目录，请检查目录权限。")
            continue
        is_directory = any(prop.find("{DAV:}resourcetype/{DAV:}collection") is not None for prop in props)
        entries[relative] = {"path": relative, "name": relative.rsplit("/", 1)[-1] if relative else "/", "is_directory": is_directory}
    if path not in entries or not entries[path]["is_directory"]:
        raise HTTPException(409, "所选位置不是可读取的真实 WebDAV 文件夹。")
    children = [entry for key, entry in entries.items() if key != path]
    return {
        "path": path,
        "directories": sorted((entry for entry in children if entry["is_directory"]), key=lambda entry: entry["name"]),
        "files": sorted((entry for entry in children if not entry["is_directory"]), key=lambda entry: entry["name"]),
    }


async def browse_patrol_webdav(config, path=""):
    async with webdav_client(config) as (client, config):
        return await read_directory(client, config, path)


async def patrol_webdav_access(config):
    async with webdav_client(config) as (client, config):
        await read_directory(client, config, config["directory"], depth=0)
        return {"ok": True, "directory": config["directory"]}


async def plan_patrol_webdav(config, target, rows):
    async with webdav_client(config) as (client, config):
        base = config["directory"]
        listing = await read_directory(client, config, base)
        folders = []

        async def choose(parent, parent_listing, preferred, aliases):
            matches = [] if parent_listing is None else [entry for entry in parent_listing["directories"] if entry["name"] in aliases]
            if len(matches) > 1:
                raise HTTPException(409, "发现多个匹配的年份或月份目录，请先在目录选择器中整理或明确保存根目录，避免保存到错误位置。")
            name = matches[0]["name"] if matches else preferred
            path = "/".join(filter(None, (parent, name)))
            if parent_listing and not matches and any(entry["name"] == name for entry in parent_listing["files"]):
                raise HTTPException(409, "目标文件夹名称已被文件占用：" + path)
            exists = bool(matches)
            folders.append({"path": path, "exists": exists})
            child = await read_directory(client, config, path) if exists else None
            return path, child

        year_path, year_listing = await choose(base, listing, f"{target.year}年", {f"{target.year}年", str(target.year)})
        month_names = {f"{target.month}月", f"{target.month:02d}月", str(target.month), f"{target.month:02d}", f"{target.year}-{target.month:02d}", f"{target.year}年{target.month}月"}
        month_path, month_listing = await choose(year_path, year_listing, f"{target.month}月", month_names)
        categories = {}
        files = []
        for row in sorted(rows, key=lambda item: item["id"]):
            category = str(row["category"])
            if safe_directory(category) != category or "/" in category or not category:
                raise HTTPException(502, "巡查记录的分类名称无效。")
            if category not in categories:
                categories[category] = await choose(month_path, month_listing, category, {category})
            parent, parent_listing = categories[category]
            filename = str(row["filename"])
            if safe_directory(filename) != filename or "/" in filename or not filename:
                raise HTTPException(502, "巡查记录的文件名无效。")
            path = parent + "/" + filename
            exists = bool(parent_listing and any(entry["name"] == filename for entry in parent_listing["files"] + parent_listing["directories"]))
            files.append({"id": row["id"], "path": path, "filename": filename, "exists": exists, "category": category, "section": row["section"]})
        if len({item["path"] for item in files}) != len(files):
            raise HTTPException(409, "记录生成了重复文件名，请重新查询。")
        return {"base_directory": base, "date": target.isoformat(), "records": len(files), "folders": folders, "files": files, "can_confirm": bool(files) and not any(item["exists"] for item in files)}


async def upload_patrol_webdav(config, plan, workbooks):
    uploaded, created = [], []
    async with webdav_client(config) as (client, config):
        try:
            for folder in plan["folders"]:
                if folder["exists"]:
                    continue
                response = await client.request("MKCOL", remote_url(config, folder["path"], directory=True))
                check_response(response, {201, 405})
                if response.status_code == 405:
                    await read_directory(client, config, folder["path"], depth=0)
                else:
                    created.append(folder["path"])
            for item in plan["files"]:
                response = await client.put(
                    remote_url(config, item["path"]), content=workbooks[item["id"]],
                    headers={"Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "If-None-Match": "*"},
                )
                check_response(response, {200, 201, 204})
                uploaded.append(item["path"])
        except (HTTPException, httpx.HTTPError) as error:
            if isinstance(error, httpx.HTTPError):
                error = HTTPException(502, "上传过程中 WebDAV 连接中断")
            if created or uploaded:
                detail = str(error.detail) + "；已新建目录：" + ("、".join(created) or "无") + "；已上传文件：" + ("、".join(uploaded) or "无") + "。未自动删除，请重新预览核对。"
                raise HTTPException(error.status_code, detail) from None
            raise error
    return {"ok": True, "records": len(uploaded), "paths": uploaded, "created_directories": created}
