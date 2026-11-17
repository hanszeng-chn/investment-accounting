# 加密静态看板发布

用户已选择浏览器本地解密方案。先生成完整只读看板，再使用 scripts/encrypted-dashboard/build.mjs 加密。发布目录仅有 AES-256-GCM 密文和解锁入口，禁止把原始 HTML、ledger.json、密码或截图放进发布目录。PBKDF2-SHA256 600000 次，32字节随机盐、12字节随机 IV，每次发布重新生成；密码从目录外权限600的文件读取，不通过命令行传入密码内容。

```sh
node <skill>/scripts/encrypted-dashboard/build.mjs --input <dashboard/index.html> --output <new-encrypted-directory> --password-file <private-password-file>
```

首次可加 --generate-password 生成32字符随机密码文件。密码文件与账本一同保留在忽略的私有数据目录，不进入版本库/部署包；不要在日志中打印。只上传已检查的 encrypted-directory，而非旧的明文 Netlify 发布包。解锁页不使用外部依赖、不保存密码，刷新/离开后锁定，不提供主动锁定按钮，解锁后直接渲染当前文档而非 Blob iframe。使用 HTTPS。加密仅保护存储内容；托管账号被入侵后仍可能被替换入口窃取密码。Netlify登录是独立的部署前提，不可宣称仅生成ZIP就已经上线。
