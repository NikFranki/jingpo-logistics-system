# FE 目录规则

继承根目录 [协作规则](../AGENTS.md)。本目录负责业务界面、登录交互和同源请求代理。

## 重点目录与入口

| 入口 | 用途 |
| --- | --- |
| `src/main.tsx`、`src/App.tsx` | 应用启动与路由 |
| `src/pages/` 及 `src/*Page.tsx` | 业务页面 |
| `src/api.ts`、`src/assistantApi.ts` | 业务与查询助手接口 |
| `src/auth/` | 登录、权限与认证请求 |
| `src/shared.tsx`、`src/theme.ts`、`src/styles.css` | 公共组件与样式 |
| `functions/`、`vite.config.ts` | 生产与开发代理 |

启动、页面和验证说明见 [README](README.md)，线上配置见 [deploy-prod.md](deploy-prod.md)，专项规格见 [文档导航](../docs/README.md)。

## 界面与接入要求

- 业务状态和操作资格以 BE 返回为准；提交后的界面应反映服务端最新结果。
- 请求需处理加载、空数据、错误与重复提交；分页和筛选保持一致。
- 计划、预测、实际时间明确区分，展示时保持时区口径一致。
- 接口变化同时核对 BE 契约；查询助手接入同时核对 Agent 身份与流式响应约定。
- 延续现有组件、主题与页面模式，避免重复封装同类能力。

## 验证与凭据保护

- 按改动运行 `npm run typecheck`、`npm run lint`、`npm run build`；交互变化补页面走查。
- Cookie 登录与 CSRF 按现有认证适配层接入；个人 Agent token 仅在内存保留。
- 不把密码、会话凭据或服务端密钥写进 localStorage、源码或前端环境变量。
- 修改 `functions/`、OAuth 回调或代理配置时，检查 Cookie、CSRF 和 SSE 转发。
