# components

界面组件目录:七件组件取自 beautifului.dev(https://www.beautifului.dev/),
站页 copy-paste 分发,MIT · Copyright (c) 2026 Shane Levine。每件文件头带
署名与本仓改动说明;设计令牌与共享样式在 `../index.css`(同出处裁剪版)。
新增依赖只按组件 imports/样式声明安装并核过许可证:shadow-plugin@2.1.0、
tailwindcss、@tailwindcss/vite(均 MIT,构建期,无新增运行时依赖)。

2026-09-02 起按操作员定稿的 v2 原型改造终端在用的五件取件(PromptBar /
StreamingText / ToolChips / CodeBlock / ApprovalCard),改动说明见各文件头;
LoadingState 与 Thinking 保留取件原样,终端暂不使用。`icons.tsx`(盾形)与
`ApprovalReceipt.tsx`(审计回执)为本仓自绘,非取件。

同日第二轮(操作员最新版设计,问答分离与审批时刻):ApprovalCard 补待答
风险描边、38px 环形倒计时与终局圆形落章,取件原作未含这些形态,为本仓
自绘延伸;`index.css` 只补 stamp-in keyframes,设计令牌未动,无新增依赖。

同日第三轮(回复 Markdown-lite 与审批 diff):CodeBlock 底色 inset→field、
Diff 视图补 hunk 行与头部复制钮、新增 diffGutter 形态(回复内 diff 围栏
行号从略);`markdownLite.tsx`(src 根,非取件)渲染回复的围栏码块与
行内码,全程 ReactNode 不走 innerHTML。仍无新增依赖。
