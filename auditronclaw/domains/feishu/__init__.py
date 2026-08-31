# feishu 域包（域模板 ADR-002 的首个实测者，03 票迁入）。
# 槽位与负空间规则见 docs/adr/ADR-002 分工表：工具实现与 register() 在
# tool.py；本域无数据形状（无 models.py）、无持久化。唯一工具
# send_feishu_summary 是绑定域工具（条件分级）——register().risk 为空是
# 设计结果，分级登记在 core 名册（域名以字面量写死，一致性由
# tests/test_domain_name_consistency.py 把守）。
