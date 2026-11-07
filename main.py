import asyncio
import contextlib
import re
import shutil
import tempfile
import weakref
from pathlib import Path

import jmcomic
from PIL import Image as PILImage

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import File, Image, Node, Nodes
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.core.config.astrbot_config import AstrBotConfig

PLUGIN_NAME = "astrbot_plugin_jmcomic"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
PDF_CHUNK_SIZE = 30

plugin_dir = Path(__file__).parent
option = jmcomic.create_option_by_file(str(plugin_dir / "option.yml"))
download_root = StarTools.get_data_dir(PLUGIN_NAME) / "Download"
option.dir_rule.base_dir = str(download_root)


def _natural_key(path: Path, root: Path):
    parts = re.split(r"(\d+)", path.relative_to(root).as_posix())
    return [
        (0, int(part)) if part.isdigit() else (1, part.lower())
        for part in parts
        if part
    ]


def _normalize_album_id(raw: str) -> str | None:
    try:
        album_id = str(jmcomic.JmcomicText.parse_to_jm_id((raw or "").strip()))
    except Exception:
        return None
    return album_id if album_id.isdigit() else None


@register("JMComic下载器", "Baizi", "JMComic下载插件", "1.2.0")
class JMComicDownloader(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._album_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    async def initialize(self):
        logger.info(f"JMComic下载插件已加载，下载目录: {download_root}")

    async def terminate(self):
        for task in self._cleanup_tasks:
            task.cancel()
        if self._cleanup_tasks:
            await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)
            self._cleanup_tasks.clear()
        logger.info("JMComic下载插件已卸载")

    def _cfg_bool(self, key: str, default: bool) -> bool:
        value = self.config.get(key, default)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _cfg_int(self, key: str, default: int, minimum: int = 0) -> int:
        try:
            return max(minimum, int(self.config.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _cfg_float(self, key: str, default: float, minimum: float = 0.0) -> float:
        try:
            return max(minimum, float(self.config.get(key, default)))
        except (TypeError, ValueError):
            return default

    def _is_pdf_mode(self) -> bool:
        return "pdf" in str(self.config.get("send_mode", "image")).lower()

    def _pdf_password(self, album_id: str) -> str | None:
        if not self._cfg_bool("pdf_encrypt", True):
            return None
        password = str(self.config.get("pdf_password", "") or "").strip()
        return password or album_id

    def _get_album_lock(self, album_id: str) -> asyncio.Lock:
        lock = self._album_locks.get(album_id)
        if lock is None:
            lock = asyncio.Lock()
            self._album_locks[album_id] = lock
        return lock

    @filter.command("jm", "JM")
    async def handle_jm_command(self, event: AstrMessageEvent):
        """下载并发送JMComic作品"""
        if self._cfg_bool("only_admin", False) and not event.is_admin():
            yield event.plain_result("只有管理员可以使用该命令。")
            return

        parts = event.message_str.split()
        if len(parts) < 2:
            yield event.plain_result("请提供JM作品ID或链接，例如：/jm 12345")
            return

        album_id = _normalize_album_id(parts[1])
        if album_id is None:
            yield event.plain_result("作品 ID 不合法，请提供数字 ID、JM+数字 或作品链接。")
            return

        async for result in self.process_jm_download(event, album_id):
            yield result

    async def process_jm_download(self, event: AstrMessageEvent, album_id: str):
        lock = self._get_album_lock(album_id)
        if lock.locked():
            yield event.plain_result("该作品正在处理中，请稍候...")
        async with lock:
            album_dir = download_root / album_id
            yield event.plain_result(f"开始下载JM作品 {album_id}，请稍候...")

            try:
                async for progress in self._download_with_progress(album_id):
                    yield event.plain_result(progress)
            except jmcomic.PartialDownloadFailedException:
                logger.exception("作品 %s 部分章节下载失败", album_id)
                if not album_dir.exists():
                    yield event.plain_result("下载失败，请检查作品 ID 后重试。")
                    return
                yield event.plain_result("部分章节下载失败，将发送已下载内容。")
            except Exception:
                logger.exception("作品 %s 下载失败", album_id)
                yield event.plain_result("下载失败，请稍后重试。")
                return

            try:
                if self._is_pdf_mode():
                    async for result in self.send_pdf(event, album_id):
                        yield result
                else:
                    async for result in self.send_images(event, album_id):
                        yield result
            except Exception:
                logger.exception("作品 %s 发送失败", album_id)
                yield event.plain_result("发送失败，请稍后重试。")

    async def _download_with_progress(self, album_id: str):
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[str] = asyncio.Queue()

        def emit(text: str):
            try:
                loop.call_soon_threadsafe(queue.put_nowait, text)
            except RuntimeError:
                pass

        class ProgressDownloader(jmcomic.JmDownloader):
            def before_album(self, album):
                super().before_album(album)
                emit(f"正在下载《{album.name}》，共 {len(album)} 章")

            def after_photo(self, photo):
                super().after_photo(photo)
                total = len(photo.from_album)
                if total > 1:
                    emit(f"章节进度：{photo.album_index}/{total}")

        def download():
            return option.download_album(
                album_id,
                downloader=ProgressDownloader,
                check_exception=True,
            )

        task = asyncio.create_task(asyncio.to_thread(download))
        try:
            while True:
                if task.done() and queue.empty():
                    break
                try:
                    yield await asyncio.wait_for(queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
            await task
            while not queue.empty():
                yield queue.get_nowait()
        finally:
            if not task.done():
                task.cancel()

    def _collect_images(self, album_dir: Path) -> list[Path]:
        if not album_dir.is_dir():
            return []
        files = [
            path
            for path in album_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ]
        files.sort(key=lambda path: _natural_key(path, album_dir))
        return files

    async def send_images(self, event: AstrMessageEvent, album_id: str):
        album_dir = download_root / album_id
        images = await asyncio.to_thread(self._collect_images, album_dir)
        if not images:
            await self._remove_dir(album_dir)
            yield event.plain_result("没有找到可发送的图片，可能下载失败。")
            return

        max_images = self._cfg_int("max_images", 80, minimum=0)
        truncated = max_images > 0 and len(images) > max_images
        selected = images[:max_images] if truncated else images
        if truncated:
            yield event.plain_result(
                f"作品共 {len(images)} 张图片，仅发送前 {max_images} 张。",
            )

        try:
            if self._cfg_bool("use_forward", False):
                nodes = [
                    Node(content=[Image.fromFileSystem(str(path))], name=f"JM{album_id}", uin="0")
                    for path in selected
                ]
                yield event.chain_result([Nodes(nodes)])
            else:
                batch_size = self._cfg_int("batch_size", 5, minimum=1)
                interval = self._cfg_float("send_interval", 0.5)
                for start in range(0, len(selected), batch_size):
                    chain = []
                    for path in selected[start:start + batch_size]:
                        try:
                            chain.append(Image.fromFileSystem(str(path)))
                        except Exception:
                            logger.exception("添加图片失败: %s", path)
                    if chain:
                        yield event.chain_result(chain)
                        if interval > 0:
                            await asyncio.sleep(interval)
        except Exception:
            logger.exception("作品 %s 图片发送失败", album_id)
            yield event.plain_result("发送图片时出错，请稍后重试。")
        finally:
            await self._remove_dir(album_dir)

    async def send_pdf(self, event: AstrMessageEvent, album_id: str):
        album_dir = download_root / album_id
        images = await asyncio.to_thread(self._collect_images, album_dir)
        if not images:
            await self._remove_dir(album_dir)
            yield event.plain_result("没有找到可发送的图片，可能下载失败。")
            return

        max_images = self._cfg_int("max_images", 80, minimum=0)
        truncated = max_images > 0 and len(images) > max_images
        selected = images[:max_images] if truncated else images

        temp_pdf = album_dir / f"{album_id}_temp.pdf"
        final_pdf = album_dir / f"{album_id}.pdf"
        password = self._pdf_password(album_id)

        try:
            if truncated:
                yield event.plain_result(
                    f"作品共 {len(images)} 张图片，PDF 仅包含前 {max_images} 张。",
                )

            await asyncio.to_thread(self._create_pdf, selected, temp_pdf)
            encrypted = await asyncio.to_thread(
                self._finalize_pdf, temp_pdf, final_pdf, password,
            )

            result = await self._build_file_result(event, final_pdf, f"{album_id}.pdf")
            yield result
            if password and encrypted:
                yield event.plain_result(f"PDF 已发送，密码：{password}")
            elif password:
                yield event.plain_result("PDF 已发送（未加密：加密失败或 pypdf 未安装）。")
            else:
                yield event.plain_result("PDF 已发送。")
        except Exception:
            logger.exception("作品 %s PDF 发送失败", album_id)
            yield event.plain_result("发送PDF时出错，请稍后重试。")
        finally:
            self._schedule_cleanup(album_dir)

    async def _build_file_result(self, event: AstrMessageEvent, path: Path, name: str):
        from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
            AiocqhttpMessageEvent,
        )

        if isinstance(event, AiocqhttpMessageEvent):
            try:
                await self._upload_file_via_api(event, path, name)
                return event.plain_result(f"已上传文件：{name}")
            except Exception:
                logger.exception("OneBot 文件接口上传失败，回退到 File 消息段")
        return event.chain_result([File(name=name, file=str(path))])

    async def _upload_file_via_api(self, event, path: Path, name: str):
        if event.is_private_chat():
            await event.bot.api.call_action(
                "upload_private_file",
                user_id=int(event.get_sender_id()),
                file=str(path),
                name=name,
            )
        else:
            await event.bot.api.call_action(
                "upload_group_file",
                group_id=int(event.get_group_id()),
                file=str(path),
                name=name,
            )

    def _create_pdf(self, image_paths: list[Path], output_path: Path):
        try:
            import img2pdf

            output_path.write_bytes(img2pdf.convert([str(p) for p in image_paths]))
            logger.info("PDF 创建成功（img2pdf）: %s, 共 %d 页", output_path, len(image_paths))
            return
        except ImportError:
            logger.warning("img2pdf 未安装，回退到 Pillow 生成 PDF")
        except Exception:
            logger.exception("img2pdf 生成失败，回退到 Pillow")

        self._create_pdf_with_pillow(image_paths, output_path)

    def _create_pdf_with_pillow(self, image_paths: list[Path], output_path: Path):
        tmp_dir = Path(tempfile.mkdtemp(prefix="jm_pdf_", dir=str(output_path.parent)))
        chunks: list[Path] = []
        page_count = 0
        try:
            for start in range(0, len(image_paths), PDF_CHUNK_SIZE):
                images = []
                try:
                    for path in image_paths[start:start + PDF_CHUNK_SIZE]:
                        try:
                            img = PILImage.open(path)
                            if img.mode != "RGB":
                                converted = img.convert("RGB")
                                img.close()
                                img = converted
                            images.append(img)
                        except Exception:
                            logger.exception("处理图片失败: %s", path)
                    if not images:
                        continue
                    chunk_path = tmp_dir / f"part_{start:05d}.pdf"
                    images[0].save(
                        chunk_path,
                        "PDF",
                        resolution=100.0,
                        save_all=True,
                        append_images=images[1:],
                    )
                    chunks.append(chunk_path)
                    page_count += len(images)
                finally:
                    for img in images:
                        with contextlib.suppress(Exception):
                            img.close()

            if not chunks:
                raise ValueError("没有有效的图片可以生成PDF")

            if len(chunks) == 1:
                shutil.move(str(chunks[0]), str(output_path))
            else:
                from pypdf import PdfWriter

                writer = PdfWriter()
                try:
                    for chunk in chunks:
                        writer.append(str(chunk))
                    with open(output_path, "wb") as output_file:
                        writer.write(output_file)
                finally:
                    with contextlib.suppress(Exception):
                        writer.close()
            logger.info(
                "PDF 创建成功（Pillow）: %s, 共 %d 页", output_path, page_count,
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _finalize_pdf(self, temp_pdf: Path, final_pdf: Path, password: str | None) -> bool:
        if password:
            if self._add_password_to_pdf(temp_pdf, final_pdf, password):
                with contextlib.suppress(OSError):
                    temp_pdf.unlink()
                return True
            if final_pdf.exists():
                final_pdf.unlink()
        shutil.move(str(temp_pdf), str(final_pdf))
        return False

    def _add_password_to_pdf(self, input_path: Path, output_path: Path, password: str) -> bool:
        try:
            from pypdf import PdfReader, PdfWriter
        except ImportError:
            logger.warning("pypdf 未安装，PDF 将不加密")
            shutil.copy(input_path, output_path)
            return False

        try:
            reader = PdfReader(str(input_path))
            writer = PdfWriter()
            try:
                for page in reader.pages:
                    writer.add_page(page)
                writer.encrypt(password)
                with open(output_path, "wb") as output_file:
                    writer.write(output_file)
            finally:
                with contextlib.suppress(Exception):
                    reader.close()
                with contextlib.suppress(Exception):
                    writer.close()
            logger.info("PDF 加密成功: %s", output_path)
            return True
        except Exception:
            logger.exception("PDF 加密失败，将发送未加密文件")
            shutil.copy(input_path, output_path)
            return False

    def _schedule_cleanup(self, album_dir: Path):
        delay = self._cfg_int("cleanup_delay", 60)

        async def cleanup():
            try:
                if delay > 0:
                    await asyncio.sleep(delay)
                await self._remove_dir(album_dir)
            except Exception:
                logger.exception("清理下载目录失败: %s", album_dir)

        task = asyncio.create_task(cleanup())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    @staticmethod
    async def _remove_dir(path: Path):
        def remove():
            shutil.rmtree(path, ignore_errors=True)

        try:
            await asyncio.to_thread(remove)
            logger.info("已清理下载文件: %s", path)
        except Exception:
            logger.exception("删除文件失败: %s", path)
