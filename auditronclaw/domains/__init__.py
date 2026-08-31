# 域包目录（域模板 ADR-002）：每个域功能一个子包 auditronclaw/domains/<name>/。
# 本 __init__.py 同时是打包必需的——缺了它 find_packages() 不收录本目录。
#
# register() 约定（槽位与负空间规则见 docs/adr/ADR-002）：
# - 每域恰好一个 register()，返回 core.domain.DomainRegistration；
# - tools 为空仅限测试夹具域，生产域至少一个工具；
# - risk 只收静态分级词汇，条件分级工具（如绑定域工具）不在此声明、留 core 名册；
# - egress 在传输定义同文件登记（register_egress_channel），这里只引用；
# - 依赖方向：domains → core 单向，core 不 import 任何域，只有装配点 import 域。
