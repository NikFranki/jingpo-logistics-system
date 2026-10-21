# Keep identity and data access authority in the backend

**Status: Accepted**

BE 是用户身份、角色、权限和数据范围的唯一权威；每次业务请求都由服务端验证。FE 只根据服务端返回的权限展示操作，不能自行授予访问权；查询 Agent 使用个人只读凭据调用 BE，不自行推断或扩大数据范围。这样登录态变化、凭据撤销和用户数据隔离都由同一处规则控制。
