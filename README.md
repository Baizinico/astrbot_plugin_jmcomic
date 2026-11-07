# JMComic下载器

一个用于 AstrBot 的 JMComic 漫画下载插件，支持图片和 PDF 两种发送方式。

## 功能特性

- 🎨 **图片模式**：按批次发送图片，可选择使用合并转发（需协议端支持）
- 📄 **PDF 模式**：将所有图片合并为 PDF 发送，支持可选密码
- 🚀 **多线程下载**：并发下载图片，失败自动重试
- 📦 **多章节支持**：按章节分目录下载，发送和 PDF 均保持正确顺序
- 🔄 **自动清理**：发送完成后自动清理临时文件
- 🛡️ **并发保护**：同一作品同时被请求时会串行处理，避免文件互删
- 📊 **进度提示**：多章节作品会提示章节下载进度

## 安装

### 依赖安装

```bash
pip install -r requirements.txt
```

依赖包：

- `jmcomic` - JMComic 下载库
- `Pillow` - 图片处理库
- `pypdf` - PDF 处理与加密
- `img2pdf` - 低内存生成 PDF（未安装时自动回退到 Pillow）

### 插件安装

将插件文件夹放置在 AstrBot 的 `data/plugins/` 目录下即可。

## 使用方法

```
/jm <作品ID>
/jm <JM+作品ID>
/jm <作品链接>
```

示例：

```
/jm 123456
/jm JM123456
/jm https://18comic.vip/album/123456
```

## 配置说明

配置文件 `_conf_schema.json` 中提供以下选项：

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `send_mode` | string | `image` | `image` 分批发送图片，`pdf` 合并为 PDF |
| `max_images` | int | `80` | 单次最多发送的图片数，`0` 表示不限制 |
| `batch_size` | int | `5` | 图片模式每条消息的图片数量 |
| `send_interval` | float | `0.5` | 图片模式每批消息的间隔（秒） |
| `use_forward` | bool | `false` | 是否使用合并转发发送图片 |
| `pdf_encrypt` | bool | `true` | 是否给 PDF 添加密码 |
| `pdf_password` | string | 空 | PDF 密码，留空时使用作品 ID |
| `cleanup_delay` | int | `60` | PDF 发送后多少秒删除本地文件 |
| `only_admin` | bool | `false` | 是否仅允许 AstrBot 管理员使用 |

## 技术细节

- 下载路径：`data/plugin_data/astrbot_plugin_jmcomic/Download/<作品ID>/<章节序号>/`
- 图片格式：下载时自动转换为 PNG
- PDF 加密：默认使用作品 ID 作为密码，可在配置中修改或关闭
- 线程配置：图片下载 8 线程，章节下载 4 线程，API 重试 3 次
- 目录规则：`Bd_Aid_Pindex`，多章节作品按章节分目录保存

## 注意事项

1. 请确保网络环境可以访问 JMComic
2. 图片和 PDF 发送完成后会自动删除下载文件
3. 建议在网络状况良好时使用，避免下载超时
4. 合并转发依赖协议端支持，若发送失败请关闭 `use_forward`

## 版本信息

- **版本**：v1.2.0
- **作者**：Baizi

## 支持

如有问题，请访问 [AstrBot帮助文档](https://astrbot.app)

## 许可证

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE) 文件。
