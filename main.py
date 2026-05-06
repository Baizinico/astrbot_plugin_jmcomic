from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core import astrbot_config, file_token_service
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.utils.astrbot_path import get_astrbot_data_path
import jmcomic
import asyncio
import os
import ssl
import certifi
import shutil
from pathlib import Path
from PIL import Image

plugin_dir = Path(__file__).parent
config_file_path = plugin_dir / 'option.yml'
option = jmcomic.create_option_by_file(str(config_file_path))

# Set download directory to an absolute path for cross-platform compatibility
download_dir = Path(get_astrbot_data_path()) / 'Download'
option.dir_rule.base_dir = str(download_dir)

ssl_context = ssl.create_default_context(cafile=certifi.where())


@register("JMComic下载器", "Baizi", "JMComic下载插件", "1.0.0")
class JMComicDownloader(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
    
    async def initialize(self):
        logger.info("JMComic下载插件已加载")
    
    async def terminate(self):
        logger.info("JMComic下载插件已卸载")
    
    async def process_jm_download(self, event: AstrMessageEvent, jm_id: str):
        try:
            yield event.plain_result(f"开始下载JM作品 {jm_id}，请稍候...")
            
            await asyncio.get_event_loop().run_in_executor(
                None, 
                lambda: option.download_album({jm_id})
            )
            
            send_mode = self.config.get("send_mode", "image")
            
            if "pdf" in send_mode.lower():
                async for result in self.send_pdf(event, jm_id):
                    yield result
            else:
                async for result in self.send_images(event, jm_id):
                    yield result
                
        except Exception as e:
            yield event.plain_result(f"处理命令时出错: {str(e)}")
    
    @filter.command("jm","JM")
    async def handle_jm_command(self, event: AstrMessageEvent):
        """下载并发送JMComic作品"""
        parts = event.message_str.split(maxsplit=1)
        if len(parts) < 2:
            yield event.plain_result("请提供JM作品ID，例如：/jm 12345")
            return

        jm_id = parts[1].strip()
        async for result in self.process_jm_download(event, jm_id):
            yield result
    
    async def send_images(self, event: AstrMessageEvent, jm_id: str):
        try:
            base_path = Path(get_astrbot_data_path()) / 'Download' / jm_id

            if not base_path.exists():
                yield event.plain_result(f"本子不存在或者下载失败力")
                return

            image_paths = sorted([
                str(base_path / f) for f in os.listdir(base_path)
                if f.lower().endswith(('.png', '.jpg', '.jpeg'))
            ])

            if not image_paths:
                yield event.plain_result("下载失败力")
                return

            from astrbot.api.message_components import Image

            batch_size = 5

            for i in range(0, len(image_paths), batch_size):
                batch = image_paths[i:i + batch_size]
                chain = []

                for path in batch:
                    try:
                        chain.append(Image.fromFileSystem(path))
                    except Exception as e:
                        logger.error(f"添加图片失败: {path} - {str(e)}")

                if chain:
                    yield event.chain_result(chain)
                    await asyncio.sleep(0.5)

            try:
                shutil.rmtree(base_path)
                logger.info(f"已删除下载文件: {base_path}")
            except Exception as e:
                logger.error(f"删除文件失败: {str(e)}")

        except Exception as e:
            yield event.plain_result(f"发送图片时出错: {str(e)}")

    async def send_pdf(self, event: AstrMessageEvent, jm_id: str):
        try:
            base_path = Path(get_astrbot_data_path()) / 'Download' / jm_id
            
            if not base_path.exists():
                yield event.plain_result(f"本子不存在或者下载失败力")
                return
            
            image_paths = sorted([
                str(base_path / f) for f in os.listdir(base_path) 
                if f.lower().endswith(('.png', '.jpg', '.jpeg'))
            ])
            
            if not image_paths:
                yield event.plain_result("下载失败力")
                return

            pdf_password = jm_id
            temp_pdf_path = base_path / f"{jm_id}_temp.pdf"
            final_pdf_path = base_path / f"{jm_id}.pdf"
            
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._create_pdf_from_images(image_paths, str(temp_pdf_path))
                )
                
                if pdf_password:
                    await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: self._add_password_to_pdf(str(temp_pdf_path), str(final_pdf_path), pdf_password)
                    )
                    os.remove(temp_pdf_path)
                else:
                    shutil.move(str(temp_pdf_path), str(final_pdf_path))
                
                file_name = f"{jm_id}.pdf"
                file_path_str = str(final_pdf_path)
                
                callback_base = astrbot_config.get("callback_api_base", "")
                if callback_base:
                    callback_base = str(callback_base).removesuffix("/")
                    token = await file_token_service.register_file(str(final_pdf_path))
                    file_url = f"{callback_base}/api/file/{token}"
                    seg = File(name=file_name, file=file_url)
                    logger.info(f"使用文件服务发送PDF: {file_url}")
                    yield event.chain_result([seg])
                else:
                    from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import AiocqhttpMessageEvent
                    if isinstance(event, AiocqhttpMessageEvent):
                        bot = event.bot
                        if event.is_private_chat():
                            await bot.api.call_action(
                                "upload_private_file",
                                user_id=int(event.get_sender_id()),
                                file=file_path_str,
                                name=file_name
                            )
                        else:
                            await bot.api.call_action(
                                "upload_group_file",
                                group_id=int(event.get_group_id()),
                                file=file_path_str,
                                name=file_name
                            )
                        yield event.plain_result(f"PDF文件 {file_name} 已上传")
                    else:
                        from astrbot.api.message_components import File
                        seg = File(name=file_name, file=file_path_str)
                        yield event.chain_result([seg])
                
            finally:
                async def delayed_cleanup():
                    try:
                        await asyncio.sleep(60)
                        if final_pdf_path.exists():
                            os.remove(final_pdf_path)
                        if temp_pdf_path.exists():
                            os.remove(temp_pdf_path)
                        shutil.rmtree(base_path)
                        logger.info(f"已清理PDF文件: {base_path}")
                    except Exception as e:
                        logger.error(f"清理PDF文件失败: {str(e)}")
                
                asyncio.create_task(delayed_cleanup())
                
        except Exception as e:
            yield event.plain_result(f"发送PDF时出错: {str(e)}")

    def _create_pdf_from_images(self, image_paths: list, output_path: str):
        images = []
        first_image = None
        
        for i, path in enumerate(image_paths):
            try:
                img = Image.open(path)
                img.verify()
                img = Image.open(path)
                
                if img.mode == 'RGBA':
                    img = img.convert('RGB')
                elif img.mode != 'RGB':
                    img = img.convert('RGB')
                
                if i == 0:
                    first_image = img
                else:
                    images.append(img)
                    
                logger.debug(f"成功处理图片: {path}")
                    
            except Exception as e:
                logger.error(f"处理图片失败: {path} - {str(e)}")
                continue
        
        if first_image:
            first_image.save(
                output_path, 
                "PDF", 
                resolution=100.0,
                save_all=True, 
                append_images=images
            )
            logger.info(f"PDF创建成功: {output_path}, 共 {len(images) + 1} 页")
        else:
            raise ValueError("没有有效的图片可以生成PDF")

    def _add_password_to_pdf(self, input_path: str, output_path: str, password: str):
        try:
            from pypdf import PdfReader, PdfWriter
            
            reader = PdfReader(input_path)
            writer = PdfWriter()
            
            for page in reader.pages:
                writer.add_page(page)
            
            writer.encrypt(password)
            
            with open(output_path, "wb") as output_file:
                writer.write(output_file)
            
            logger.info(f"PDF加密成功: {output_path}")
            
        except ImportError:
            logger.warning("pypdf库未安装，跳过PDF加密，直接复制文件")
            shutil.copy(input_path, output_path)
        except Exception as e:
            logger.error(f"PDF加密失败: {str(e)}")
            shutil.copy(input_path, output_path)
