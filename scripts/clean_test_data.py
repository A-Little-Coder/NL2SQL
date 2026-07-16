"""清理 E2E 累积的测试数据（e2e_*/curl_*），保留 init_auth 种子数据"""

import sqlite3
from pathlib import Path

db = Path(__file__).parent.parent / "auth" / "table_field_acl.db"
c = sqlite3.connect(str(db))
for sql in [
    "DELETE FROM deny_rules WHERE role_id LIKE 'e2e_%' OR role_id IN ('curl_r','e2e_role')",
    "DELETE FROM user_roles WHERE user_id LIKE 'e2e_%' OR user_id='curl_r'",
    "DELETE FROM users WHERE user_id LIKE 'e2e_%'",
    "DELETE FROM roles WHERE role_id LIKE 'e2e_%' OR role_id IN ('curl_r','e2e_role')",
]:
    c.execute(sql)
c.commit()
c.close()
print("cleaned e2e_*/curl_* test data, 种子数据保留")
