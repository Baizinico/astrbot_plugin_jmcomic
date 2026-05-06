from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.core.config.astrbot_config import AstrBotConfig
import pathlib
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
            base_path = pathlib.Path('/AstrBot/data/Download') / jm_id
            
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
            
            from astrbot.api.message_components import Node, Plain, Image
            
            nodes = []
            batch_size = 10
            
            for i in range(0, len(image_paths), batch_size):
                batch = image_paths[i:i + batch_size]
                content = []
                
                for path in batch:
                    try:
                        content.append(Image.fromFileSystem(path))
                    except Exception as e:
                        logger.error(f"添加图片失败: {path} - {str(e)}")
                        content.append(Plain(f"[图片加载失败: {os.path.basename(path)}]"))
                
                image_node = Node(
                    uin=725699515,
                    name="爱你喵",
                    content=content
                )
                nodes.append(image_node)

            try:
                yield event.chain_result(nodes)
            except Exception as e:
                logger.error(f"发送图片消息失败: {str(e)}")
                yield event.plain_result(f"发送图片失败，可能是由于消息过长或网络问题: {str(e)}")

            try:
                shutil.rmtree(base_path)
                logger.info(f"已删除下载文件: {base_path}")
            except Exception as e:
                logger.error(f"删除文件失败: {str(e)}")
                
        except Exception as e:
            yield event.plain_result(f"发送图片时出错: {str(e)}")

    async def send_pdf(self, event: AstrMessageEvent, jm_id: str):
        try:
            base_path = pathlib.Path('/AstrBot/data/Download') / jm_id
            
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
                
                from astrbot.api.message_components import File
                file_name = f"{jm_id}.pdf"
                seg = File(name=file_name, file=str(final_pdf_path))
                yield event.chain_result([seg])
                
                file_size_mb = final_pdf_path.stat().st_size / (1024 * 1024)
                wait_time = max(5, int(file_size_mb * 2))
                logger.info(f"PDF大小: {file_size_mb:.2f}MB，等待{wait_time}秒后清理文件")
                await asyncio.sleep(wait_time)
                
            finally:
                try:
                    if final_pdf_path.exists():
                        os.remove(final_pdf_path)
                        logger.info(f"已删除PDF文件: {final_pdf_path}")
                    if temp_pdf_path.exists():
                        os.remove(temp_pdf_path)
                except Exception as e:
                    logger.error(f"删除PDF文件失败: {str(e)}")
                
                try:
                    shutil.rmtree(base_path)
                    logger.info(f"已删除下载文件夹: {base_path}")
                except Exception as e:
                    logger.error(f"删除文件夹失败: {str(e)}")
                
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
