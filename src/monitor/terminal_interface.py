# ============================================================================
# Terminal 交互界面实现
# ============================================================================


import sys


class TerminalInterface:
    """Terminal 交互式界面 - 流式输出 NL2SQL 处理过程"""

    def __init__(self, monitor=None):
        self.monitor = monitor or type('obj', (object,), {'enabled': False})()
        self.stream_callback = None

    def set_stream_callback(self, callback):
        """设置流式输出回调函数"""
        self.stream_callback = callback

    def _print(self, message: str):
        """打印并刷新缓冲区（用于流式输出）"""
        print(message, flush=True)

    def display_header(self):
        """显示系统标题"""
        self._print("")
        self._print("=" * 50)
        self._print("   NL2SQL Agent System")
        self._print("=" * 50)
        self._print("")

    def display_user_query(self, query: str):
        """显示用户查询"""
        self._print(f"用户：{query}")
        self._print("")

    def display_stage(self, stage_name: str, status: str = "->"):
        """显示当前阶段状态"""
        symbols = {"->": "[→]", "done": "[✓]", "error": "[✗]"}
        symbol = symbols.get(status, status)
        self._print(f"{symbol} {stage_name}")

    def display_substage(self, substage: str):
        """显示子阶段"""
        self._print(f"    - {substage}")

    def display_sql(self, sql: str):
        """显示生成的 SQL"""
        self._print("")
        self._print("生成的 SQL:")
        self._print("-" * 40)
        for line in sql.strip().split("\n"):
            self._print(f"  {line}")
        self._print("-" * 40)

    def display_result_table(self, headers: list, rows: list):
        """以表格形式显示结果"""
        if not rows:
            self._print("结果为空")
            return

        self._print("")
        self._print("执行结果:")

        # 计算每列宽度
        widths = [len(str(h)) for h in headers]
        for row in rows:
            for i, cell in enumerate(row):
                if i < len(widths):
                    widths[i] = max(widths[i], len(str(cell)))

        # 绘制表格
        def make_row(cells):
            return "| " + " | ".join(str(c).ljust(w) for c, w in zip(cells, widths)) + " |"

        def make_separator():
            return "+" + "+".join("-" * (w + 2) for w in widths) + "+"

        self._print(make_separator())
        self._print(make_row(headers))
        self._print(make_separator())
        for row in rows:
            self._print(make_row(row))
        self._print(make_separator())

    def display_error(self, error: str):
        """显示错误信息"""
        self._print("")
        self._print("[!] 发生错误:")
        self._print(f"    {error}")

    def display_duration(self, duration: float):
        """显示总耗时"""
        self._print("")
        self._print(f"总计耗时：{duration:.2f} 秒")
        self._print("")

    def interactive_loop(self):
        """交互式循环主函数"""
        self.display_header()
        self._print("输入自然语言查询（输入 'quit' 退出）:")
        self._print("")

        while True:
            try:
                query = input("您：").strip()
                if query.lower() in ['quit', 'exit', 'q']:
                    self._print("再见！")
                    break
                if not query:
                    continue

                # TODO: 这里调用 NL2SQL 主流程
                # result = nl2sql_pipeline.run(query)

            except KeyboardInterrupt:
                self._print("\n中断，再见！")
                break
            except Exception as e:
                self.display_error(str(e))