from __future__ import annotations

import io
import re
import time
from contextlib import contextmanager
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Callable, Iterator, Mapping
from urllib.parse import parse_qs, urljoin, urlsplit

import httpx


LTI_TOOL_ID = 8329
LTI_HOSTS = frozenset({"oc.sjtu.edu.cn", "v.sjtu.edu.cn"})
PLAYBACK_HOSTS = frozenset({"live.sjtu.edu.cn", "videos.sjtu.edu.cn"})
SLIDE_IMAGE_HOSTS = PLAYBACK_HOSTS
SLIDE_IMAGE_TYPES = frozenset(("image/jpeg", "image/png"))
VIDEO_API_BASE = "https://v.sjtu.edu.cn/jy-application-resourcemanage"
VIDEO_REFERER = "https://v.sjtu.edu.cn/jy-application-resourcemanage-ui/"
REMOTE_SOURCE_ID = re.compile(r"sjtu-video:([1-9][0-9]{0,18}):([1-9][0-9]{0,18})")
_NUMERIC_ID = re.compile(r"[1-9][0-9]{0,18}")
_JWT_TOKEN = re.compile(r"[A-Za-z0-9._~+-]{16,16384}")


class SJTUVideoError(RuntimeError):
    """可安全展示的上海交大课程视频服务错误。"""


def _strict_id(value: int | str, label: str) -> str:
    if isinstance(value, bool) or type(value) not in {int, str}:
        raise SJTUVideoError(f"{label}不正确。")
    result = str(value) if type(value) is int else value
    if _NUMERIC_ID.fullmatch(result) is None:
        raise SJTUVideoError(f"{label}不正确。")
    return result


def parse_remote_source_id(source_id: str) -> tuple[str, str]:
    if type(source_id) is not str:
        raise SJTUVideoError("远程视频标识不正确。")
    match = REMOTE_SOURCE_ID.fullmatch(source_id)
    if match is None:
        raise SJTUVideoError("远程视频标识不正确。")
    return match.group(1), match.group(2)


def _validate_https_url(value: Any, hosts: frozenset[str], label: str) -> str:
    if type(value) is not str or not value or len(value) > 8192:
        raise SJTUVideoError(f"{label}不安全，已停止处理。")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SJTUVideoError(f"{label}不安全，已停止处理。") from exc
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname not in hosts
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise SJTUVideoError(f"{label}不安全，已停止处理。")
    return value


def _extract_jwt(value: str) -> str | None:
    parsed = urlsplit(value)
    fragment_query = parsed.fragment.split("?", 1)[-1]
    for component in (parsed.query, parsed.fragment, fragment_query):
        for candidate in parse_qs(component, keep_blank_values=True).get("jwt_token", ()):
            if _JWT_TOKEN.fullmatch(candidate):
                return candidate
    return None


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict[str, Any]] = []
        self._form: dict[str, Any] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "form":
            self._form = {
                "action": attributes.get("action", ""),
                "method": attributes.get("method", "get").lower(),
                "fields": [],
            }
            self.forms.append(self._form)
        elif tag.lower() == "input" and self._form is not None:
            name = attributes.get("name")
            if name and len(self._form["fields"]) < 100:
                self._form["fields"].append(
                    (name, attributes.get("value", ""))
                )

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self._form = None


class SJTUVideoService:
    """只读访问 SJTU 课程录像；LTI JWT 仅在单次调用内存中使用。"""

    def __init__(
        self,
        canvas_client_factory: Callable[[], Any],
        *,
        http_client_factory: Callable[[], Any] | None = None,
        timeout: float = 20.0,
        max_launch_steps: int = 8,
        max_slide_pages: int = 200,
        max_slide_image_bytes: int = 15728640,
        max_slides_total_bytes: int = 209715200,
        max_slide_pixels: int = 40_000_000,
        max_slides_total_pixels: int = 500_000_000,
        image_retries: int = 2,
    ) -> None:
        self.canvas_client_factory = canvas_client_factory
        self.http_client_factory = http_client_factory
        self.timeout = timeout
        self.max_launch_steps = max_launch_steps
        if not 1 <= max_slide_pages <= 500:
            raise ValueError("max_slide_pages must be between 1 and 500")
        if max_slide_image_bytes <= 0 or max_slides_total_bytes <= 0:
            raise ValueError("slide byte limits must be positive")
        if (
            max_slide_pixels <= 0
            or max_slides_total_pixels < max_slide_pixels
            or not 0 <= image_retries <= 5
        ):
            raise ValueError("slide image limits are invalid")
        self.max_slide_pages = max_slide_pages
        self.max_slide_image_bytes = max_slide_image_bytes
        self.max_slides_total_bytes = max_slides_total_bytes
        self.max_slide_pixels = max_slide_pixels
        self.max_slides_total_pixels = max_slides_total_pixels
        self.image_retries = image_retries

    @staticmethod
    def remote_source_id(
        canvas_course_id: int | str, video_id: int | str
    ) -> str:
        return (
            f"sjtu-video:{_strict_id(canvas_course_id, '课程 ID')}:"
            f"{_strict_id(video_id, '视频 ID')}"
        )

    @contextmanager
    def _http_client(self) -> Iterator[Any]:
        client = (
            self.http_client_factory()
            if self.http_client_factory is not None
            else httpx.Client(
                timeout=httpx.Timeout(self.timeout),
                follow_redirects=False,
                headers={"User-Agent": "SJTU-Learning-Assistant/Video"},
            )
        )
        try:
            yield client
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def _canvas_launch_url(self, course_id: str) -> str:
        path = (
            f"/api/v1/courses/{course_id}/external_tools/sessionless_launch"
        )
        params = {
            "id": str(LTI_TOOL_ID),
            "launch_type": "course_navigation",
        }
        try:
            canvas = self.canvas_client_factory()
            request = getattr(canvas, "_request", None)
            if callable(request):
                response = request("GET", path, params=params)
            else:
                response = canvas.request(
                    "GET", path, params=params, follow_redirects=False
                )
            response.raise_for_status()
            payload = response.json()
        except SJTUVideoError:
            raise
        except Exception as exc:
            raise SJTUVideoError(
                "无法使用 Canvas 在线入口访问课程视频。"
            ) from exc
        if not isinstance(payload, Mapping):
            raise SJTUVideoError("Canvas 返回了无法识别的视频入口。")
        launch_url = payload.get("url") or payload.get("launch_url")
        return _validate_https_url(launch_url, LTI_HOSTS, "LTI 启动链接")

    def _launch_jwt(self, course_id: str, client: Any) -> str:
        url = self._canvas_launch_url(course_id)
        method = "GET"
        data: list[tuple[str, str]] | None = None
        for _ in range(self.max_launch_steps):
            token = _extract_jwt(url)
            if token:
                return token
            try:
                response = client.request(
                    method,
                    url,
                    data=dict(data) if data is not None else None,
                    headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "Referer": url,
                    },
                    follow_redirects=False,
                )
            except Exception as exc:
                raise SJTUVideoError("课程视频平台登录请求失败。") from exc
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise SJTUVideoError("课程视频平台未返回有效跳转地址。")
                url = _validate_https_url(
                    urljoin(url, location), LTI_HOSTS, "LTI 跳转链接"
                )
                if response.status_code in (301, 302, 303):
                    method, data = "GET", None
                continue
            if response.status_code >= 400:
                raise SJTUVideoError(
                    "课程视频平台登录失败，请重新同步 Canvas 凭据。"
                )
            response_url = _validate_https_url(
                str(response.url), LTI_HOSTS, "LTI 响应链接"
            )
            token = _extract_jwt(response_url)
            if token:
                return token
            text = response.text
            if len(text) > 2_000_000:
                raise SJTUVideoError("课程视频平台返回内容过大，已停止处理。")
            parser = _FormParser()
            try:
                parser.feed(text)
            except Exception as exc:
                raise SJTUVideoError(
                    "课程视频平台返回了无法识别的登录页面。"
                ) from exc
            form = next(
                (
                    item
                    for item in parser.forms
                    if item["method"] in {"get", "post"}
                ),
                None,
            )
            if form is None:
                raise SJTUVideoError("课程视频平台登录流程未返回有效凭据。")
            url = _validate_https_url(
                urljoin(response_url, form["action"]),
                LTI_HOSTS,
                "LTI 表单地址",
            )
            data = form["fields"]
            if any(
                len(name) > 256 or len(value) > 65_536
                for name, value in data
            ):
                raise SJTUVideoError("课程视频平台登录表单不正确。")
            method = form["method"].upper()
            if method == "GET":
                url = str(httpx.URL(url).copy_merge_params(data))
                data = None
        raise SJTUVideoError("课程视频平台登录跳转次数过多。")

    def _api_json(
        self,
        client: Any,
        token: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{VIDEO_API_BASE}{path}"
        try:
            response = client.request(
                "GET",
                url,
                params=params,
                headers={
                    "Accept": "application/json",
                    "jwt-token": token,
                    "Referer": VIDEO_REFERER,
                },
                follow_redirects=False,
            )
            if response.status_code in (401, 403):
                raise SJTUVideoError("课程视频平台登录已失效，请重试。")
            response.raise_for_status()
            payload = response.json()
        except SJTUVideoError:
            raise
        except Exception as exc:
            raise SJTUVideoError("课程视频平台请求失败，请稍后重试。") from exc
        if isinstance(payload, Mapping):
            success = payload.get("success")
            code = payload.get("code")
            if success is False or (
                code is not None and code not in (0, 200, "0", "200")
            ):
                raise SJTUVideoError(
                    "课程视频平台拒绝了请求，请重新登录后重试。"
                )
        if not isinstance(payload, (Mapping, list)):
            raise SJTUVideoError("课程视频平台返回了无法识别的数据。")
        return payload

    @staticmethod
    def _payload_data(payload: Any) -> Any:
        current = payload
        for _ in range(4):
            if not isinstance(current, Mapping):
                return current
            next_value = next(
                (
                    current[key]
                    for key in ("data", "result")
                    if key in current and current[key] is not None
                ),
                current,
            )
            if next_value is current:
                return current
            current = next_value
        return current

    def _teaching_class_id(self, payload: Any) -> str:
        current = self._payload_data(payload)
        if isinstance(current, Mapping):
            record = current.get("canvasRecord", current)
            if isinstance(record, Mapping):
                return _strict_id(record.get("teachingClassId"), "教学班 ID")
        raise SJTUVideoError("课程视频平台未返回教学班信息。")

    def _video_records(self, payload: Any) -> list[Mapping[str, Any]]:
        current = self._payload_data(payload)
        for _ in range(4):
            if isinstance(current, list):
                if all(isinstance(item, Mapping) for item in current):
                    return list(current)
                break
            if not isinstance(current, Mapping):
                break
            next_value = next(
                (
                    current[key]
                    for key in ("records", "content", "list", "rows", "items")
                    if key in current
                ),
                None,
            )
            if next_value is None:
                break
            current = next_value
        raise SJTUVideoError("课程视频平台返回了无法识别的录像列表。")

    @staticmethod
    def _first(record: Mapping[str, Any], *keys: str) -> Any:
        return next(
            (record[key] for key in keys if record.get(key) not in (None, "")),
            None,
        )

    @staticmethod
    def _duration(value: Any) -> int | None:
        if type(value) in {int, float} and 0 <= value <= 24 * 60 * 60:
            return int(value)
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.isdigit() and int(stripped) <= 24 * 60 * 60:
                return int(stripped)
            match = re.fullmatch(
                r"(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2})", stripped
            )
            if match:
                hours, minutes, seconds = (
                    int(part or 0) for part in match.groups()
                )
                if minutes < 60 and seconds < 60:
                    return hours * 3600 + minutes * 60 + seconds
        return None

    @staticmethod
    def _recorded_at(value: Any) -> str | None:
        if isinstance(value, str):
            result = value.strip()
            if (
                result
                and len(result) <= 64
                and not any(ord(character) < 32 for character in result)
            ):
                return result
        return None

    @staticmethod
    def _safe_text(value: Any, limit: int = 300) -> str | None:
        if value is None or isinstance(value, (Mapping, list, tuple)):
            return None
        text = " ".join(str(value).split()).strip()
        if not text or any(ord(character) < 32 for character in text):
            return None
        return text[:limit]

    @staticmethod
    def _positive_number(value: Any, maximum: int = 10000) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            return None
        return number if 1 <= number <= maximum else None

    @staticmethod
    def _weekday(value: Any) -> tuple[int | None, str | None]:
        number = SJTUVideoService._positive_number(value, 7)
        if number is None:
            return None, None
        return number, ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[number - 1]

    @staticmethod
    def _duration_between(begin: Any, end: Any) -> int | None:
        if not isinstance(begin, str) or not isinstance(end, str):
            return None
        for fmt in (None, "%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%H:%M:%S", "%H:%M"):
            try:
                if fmt is None:
                    start = datetime.fromisoformat(begin.strip().replace("Z", "+00:00"))
                    finish = datetime.fromisoformat(end.strip().replace("Z", "+00:00"))
                else:
                    start = datetime.strptime(begin.strip(), fmt)
                    finish = datetime.strptime(end.strip(), fmt)
            except ValueError:
                continue
            seconds = int((finish - start).total_seconds())
            return seconds if 0 <= seconds <= 24 * 60 * 60 else None
        return None

    @staticmethod
    def _milliseconds(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        try:
            result = int(float(value))
        except (TypeError, ValueError, OverflowError):
            return None
        return result if 0 <= result <= 24 * 60 * 60 * 1000 else None

    @staticmethod
    def _vtt_time(milliseconds: int) -> str:
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"

    @staticmethod
    def _vtt_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        text = "\n".join(part.strip() for part in text.split("\n") if part.strip())
        if not text:
            return None
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")[:10_000]
        )

    @staticmethod
    def _processing(payload: Mapping[str, Any]) -> bool:
        values = (
            payload.get("status"),
            payload.get("state"),
            payload.get("processStatus"),
        )
        processing_values = {
            "processing",
            "pending",
            "running",
            "generating",
            "处理中",
            "生成中",
        }
        return any(str(value).strip().casefold() in processing_values for value in values)

    def subtitles(self, source_id: str) -> dict[str, Any]:
        course_id, video_id = parse_remote_source_id(source_id)
        with self._http_client() as client:
            token = self._launch_jwt(course_id, client)
            payload = self._api_json(
                client,
                token,
                f"/v1/course/ai/translate/{video_id}",
                params={"useOriginal": "true"},
            )
        current = self._payload_data(payload)
        if not isinstance(current, Mapping):
            raise SJTUVideoError("字幕服务返回了无法识别的数据。")
        preferred = current.get("afterAssemblyList")
        fallback = current.get("beforeAssemblyList")
        raw_cues = preferred if isinstance(preferred, list) and preferred else fallback
        if not isinstance(raw_cues, list) or not raw_cues:
            processing = self._processing(current)
            return {
                "status": "processing" if processing else "empty",
                "message": (
                    "字幕正在生成，请稍后重试。" if processing else "该录像暂无字幕。"
                ),
                "content_type": "text/vtt; charset=utf-8",
                "vtt": None,
                "cue_count": 0,
            }
        cues: list[tuple[int, int, str]] = []
        for raw in raw_cues:
            if not isinstance(raw, Mapping):
                continue
            begin = self._milliseconds(raw.get("bg"))
            end = self._milliseconds(raw.get("ed"))
            text = self._vtt_text(raw.get("res"))
            if begin is None or end is None or end <= begin or text is None:
                continue
            cues.append((begin, end, text))
        cues.sort(key=lambda cue: (cue[0], cue[1]))
        if not cues:
            return {
                "status": "empty",
                "message": "该录像暂无可用字幕。",
                "content_type": "text/vtt; charset=utf-8",
                "vtt": None,
                "cue_count": 0,
            }
        blocks = ["WEBVTT"]
        for begin, end, text in cues:
            blocks.append(
                f"{self._vtt_time(begin)} --> {self._vtt_time(end)}\n{text}"
            )
        return {
            "status": "ready",
            "message": "字幕已加载。",
            "content_type": "text/vtt; charset=utf-8",
            "vtt": "\n\n".join(blocks) + "\n",
            "cue_count": len(cues),
        }

    def _slide_records(self, payload: Any) -> tuple[list[dict[str, Any]], str]:
        current = self._payload_data(payload)
        if not isinstance(current, Mapping):
            raise SJTUVideoError("课件服务返回了无法识别的数据。")
        raw_documents = current.get("docList")
        if not isinstance(raw_documents, list) or not raw_documents:
            status = "processing" if self._processing(current) else "empty"
            return [], status
        if len(raw_documents) > self.max_slide_pages:
            raise SJTUVideoError("课件页数超过安全限制，已停止生成 PDF。")
        records: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_documents):
            if not isinstance(raw, Mapping):
                raise SJTUVideoError("课件页面信息不完整，已停止生成 PDF。")
            url = _validate_https_url(
                raw.get("imageUrl"), SLIDE_IMAGE_HOSTS, "课件图片地址"
            )
            seek = self._milliseconds(raw.get("imageSeekTime"))
            snapshot = self._milliseconds(raw.get("imageSnapshotTime"))
            records.append(
                {
                    "url": url,
                    "index": index,
                    "order_time": seek if seek is not None else snapshot,
                }
            )
        records.sort(
            key=lambda item: (
                item["order_time"] is None,
                item["order_time"] or 0,
                item["index"],
            )
        )
        return records, "ready"

    def slides_metadata(self, source_id: str) -> dict[str, Any]:
        course_id, video_id = parse_remote_source_id(source_id)
        with self._http_client() as client:
            token = self._launch_jwt(course_id, client)
            payload = self._api_json(
                client,
                token,
                "/v1/course/ai/ppt",
                params={"courseId": video_id},
            )
        records, status = self._slide_records(payload)
        message = (
            "课件已就绪。"
            if status == "ready"
            else (
                "课件正在生成，请稍后重试。"
                if status == "processing"
                else "该录像暂无课件。"
            )
        )
        return {"status": status, "message": message, "pages": records}

    def _download_slide_image(self, client: Any, url: str) -> bytes:
        for attempt in range(self.image_retries + 1):
            try:
                with client.stream(
                    "GET",
                    url,
                    headers={
                        "Accept": "image/jpeg,image/png",
                        "Referer": VIDEO_REFERER,
                    },
                    follow_redirects=False,
                ) as response:
                    if response.status_code in (429, 500, 502, 503, 504):
                        raise httpx.HTTPStatusError(
                            "temporary slide image failure",
                            request=response.request,
                            response=response,
                        )
                    response.raise_for_status()
                    content_type = (
                        response.headers.get("content-type", "")
                        .split(";", 1)[0]
                        .strip()
                        .casefold()
                    )
                    if content_type not in SLIDE_IMAGE_TYPES:
                        raise SJTUVideoError(
                            "课件图片类型不受支持，已停止生成 PDF。"
                        )
                    content_length = response.headers.get("content-length")
                    if (
                        content_length
                        and content_length.isdigit()
                        and int(content_length) > self.max_slide_image_bytes
                    ):
                        raise SJTUVideoError(
                            "单张课件图片超过大小限制，已停止生成 PDF。"
                        )
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.max_slide_image_bytes:
                            raise SJTUVideoError(
                                "单张课件图片超过大小限制，已停止生成 PDF。"
                            )
                        chunks.append(chunk)
                    if not chunks:
                        raise SJTUVideoError(
                            "课件图片内容为空，已停止生成 PDF。"
                        )
                    return b"".join(chunks)
            except SJTUVideoError:
                raise
            except Exception as exc:
                if attempt >= self.image_retries:
                    raise SJTUVideoError(
                        "课件图片下载失败，请稍后重试。"
                    ) from exc
                time.sleep(0.05 * (attempt + 1))
        raise SJTUVideoError("课件图片下载失败，请稍后重试。")

    def _render_slides_pdf(self, payloads: list[bytes]) -> bytes:
        try:
            from PIL import Image, UnidentifiedImageError
        except ImportError as exc:
            raise SJTUVideoError("当前环境缺少 PDF 图像组件。") from exc
        pages: list[Any] = []
        total_pixels = 0
        try:
            for payload in payloads:
                try:
                    source = Image.open(io.BytesIO(payload))
                    pixels = source.width * source.height
                    total_pixels += pixels
                    if (
                        source.width <= 0
                        or source.height <= 0
                        or pixels > self.max_slide_pixels
                        or total_pixels > self.max_slides_total_pixels
                    ):
                        source.close()
                        raise SJTUVideoError(
                            "课件图片像素超过安全限制，已停止生成 PDF。"
                        )
                    source.load()
                except (OSError, ValueError, UnidentifiedImageError) as exc:
                    raise SJTUVideoError(
                        "课件图片格式无效，已停止生成 PDF。"
                    ) from exc
                if source.mode in ("RGBA", "LA"):
                    page = Image.new("RGB", source.size, "white")
                    alpha = source.getchannel("A")
                    page.paste(source.convert("RGB"), mask=alpha)
                    source.close()
                else:
                    page = source.convert("RGB")
                    source.close()
                pages.append(page)
            output = io.BytesIO()
            pages[0].save(
                output,
                format="PDF",
                save_all=True,
                append_images=pages[1:],
                resolution=150.0,
            )
            return output.getvalue()
        except SJTUVideoError:
            raise
        except Exception as exc:
            raise SJTUVideoError("课件 PDF 生成失败，请稍后重试。") from exc
        finally:
            for page in pages:
                page.close()

    def slides_pdf(self, source_id: str) -> dict[str, Any]:
        course_id, video_id = parse_remote_source_id(source_id)
        with self._http_client() as client:
            token = self._launch_jwt(course_id, client)
            payload = self._api_json(
                client,
                token,
                "/v1/course/ai/ppt",
                params={"courseId": video_id},
            )
            records, status = self._slide_records(payload)
            if not records:
                if status == "processing":
                    raise SJTUVideoError("课件正在生成，请稍后重试。")
                raise SJTUVideoError("该录像暂无课件。")
            images: list[bytes] = []
            total = 0
            for record in records:
                image = self._download_slide_image(client, record["url"])
                total += len(image)
                if total > self.max_slides_total_bytes:
                    raise SJTUVideoError(
                        "课件图片总大小超过安全限制，已停止生成 PDF。"
                    )
                images.append(image)
        pdf = self._render_slides_pdf(images)
        return {
            "status": "created",
            "data": pdf,
            "page_count": len(images),
            "size": len(pdf),
        }

    def _map_video(
        self,
        record: Mapping[str, Any],
        course_id: str,
        course_name: str | None,
    ) -> dict[str, Any]:
        video_id = _strict_id(
            self._first(record, "id", "courseId", "videoId", "vodId"),
            "视频 ID",
        )
        mapped_course_name = self._safe_text(
            self._first(record, "subjName", "courName", "courseName")
        ) or self._safe_text(course_name) or "课程"
        teaching_class = self._safe_text(
            self._first(record, "teclName", "teachingClassName", "className")
        )
        week_number = self._positive_number(
            self._first(record, "weekNo", "weekNumber"), 60
        )
        weekday, weekday_label = self._weekday(record.get("week"))
        lesson_number = self._positive_number(record.get("letiNumber"), 30)
        begin_time = self._recorded_at(
            self._first(
                record,
                "courBeginTime",
                "beginTime",
                "startTime",
                "recordTime",
                "createTime",
            )
        )
        end_time = self._recorded_at(
            self._first(record, "courEndTime", "endTime")
        )
        classroom = self._safe_text(record.get("clroName"), 120)
        title_parts = [mapped_course_name]
        if week_number is not None:
            title_parts.append(f"第{week_number}周")
        if weekday_label:
            title_parts.append(weekday_label)
        if lesson_number is not None:
            title_parts.append(f"第{lesson_number}节")
        if len(title_parts) == 1:
            title_parts.append("课程录像")
        name = " · ".join(title_parts)
        duration = self._duration(
            self._first(record, "duration", "videoDuration", "courDuration")
        )
        if duration is None:
            duration = self._duration_between(begin_time, end_time)
        source_id = self.remote_source_id(course_id, video_id)
        return {
            "source_id": source_id,
            "name": name,
            "media_kind": "video",
            "content_type": "video/mp4",
            "size": None,
            "source": "video_space",
            "course_name": mapped_course_name,
            "teaching_class": teaching_class,
            "week_number": week_number,
            "weekday": weekday,
            "weekday_label": weekday_label,
            "lesson_number": lesson_number,
            "recorded_at": begin_time,
            "ended_at": end_time,
            "classroom": classroom,
            "duration": duration,
            "downloadable": False,
            "supports_subtitle": True,
            "supports_slides_pdf": True,
            "capabilities": {
                "subtitles": {"available": True, "status": "on_demand"},
                "slides_pdf": {"available": True, "status": "on_demand"},
            },
            "playback": {
                "available": True,
                "status": "ready",
                "transport": "dashboard_action",
                "action": "play_remote_video",
                "source_id": source_id,
                "media_kind": "video",
                "content_type": "video/mp4",
                "name": name,
            },
            "subtitles": [],
        }

    def list_course_videos(
        self,
        canvas_course_id: int | str,
        *,
        course_name: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        course_id = _strict_id(canvas_course_id, "课程 ID")
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise SJTUVideoError("录像数量限制不正确。")
        with self._http_client() as client:
            token = self._launch_jwt(course_id, client)
            context = self._api_json(client, token, "/lms/launch-context")
            teaching_class_id = self._teaching_class_id(context)
            payload = self._api_json(
                client,
                token,
                "/v1/subject_vod_list_new",
                params={
                    "page.pageIndex": 1,
                    "page.pageSize": limit,
                    "teclIds": teaching_class_id,
                    "page.orders[0].asc": "false",
                    "page.orders[0].field": "courBeginTime",
                    "schoolOpenStatusFlag": "false",
                },
            )
        return [
            self._map_video(record, course_id, course_name)
            for record in self._video_records(payload)
        ]

    def playback(self, source_id: str) -> dict[str, Any]:
        course_id, video_id = parse_remote_source_id(source_id)
        with self._http_client() as client:
            token = self._launch_jwt(course_id, client)
            payload = self._api_json(
                client,
                token,
                "/v1/course_vod_urls_new",
                params={"courseId": video_id},
            )
        current = self._payload_data(payload)
        raw_urls = (
            current.get("courseVodViewList")
            if isinstance(current, Mapping)
            else None
        )
        if not isinstance(raw_urls, list) or not raw_urls:
            raise SJTUVideoError("课程录像暂时没有可用播放地址。")
        urls: list[str] = []
        for item in raw_urls:
            value = item.get("url") if isinstance(item, Mapping) else item
            url = _validate_https_url(value, PLAYBACK_HOSTS, "录像播放地址")
            if url not in urls:
                urls.append(url)
        if not urls:
            raise SJTUVideoError("课程录像暂时没有可用播放地址。")
        return {
            "available": True,
            "status": "ready",
            "transport": "remote_url",
            "action": "play_remote_video",
            "source_id": source_id,
            "media_kind": "video",
            "content_type": "video/mp4",
            "url": urls[0],
            "urls": urls,
        }


__all__ = ["SJTUVideoError", "SJTUVideoService", "parse_remote_source_id"]
