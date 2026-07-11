/**
 * SQL 代码块 + 复制按钮（任务 12.2，决策 D7）
 *
 * 职责：
 * - 以等宽字体 `<pre>` 展示 SQL 代码，保留换行与缩进
 * - 复制按钮调用 navigator.clipboard.writeText，成功/失败用 AntD message 反馈
 * - 通过 App.useApp() 获取 message 实例，确保使用全局 ConfigProvider 主题
 *
 * props: { sql: string }
 */
import { useState } from 'react';
import { App, Button, Typography } from 'antd';
import { CopyOutlined, CheckOutlined } from '@ant-design/icons';

const { Text } = Typography;

export default function SqlBlock({ sql }: { sql: string }) {
  const { message } = App.useApp();
  const [copied, setCopied] = useState(false);

  /** 复制 SQL 到剪贴板，并给出成功/失败反馈 */
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      message.success('已复制');
      setCopied(true);
      // 2 秒后恢复按钮状态
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      message.error('复制失败，请手动选择文本复制');
    }
  };

  return (
    <div
      style={{
        position: 'relative',
        margin: '8px 0',
        borderRadius: 6,
        background: '#f5f5f5',
        border: '1px solid #e8e8e8',
        overflow: 'hidden',
      }}
    >
      {/* 右上角复制按钮 */}
      <Button
        size="small"
        type="text"
        icon={copied ? <CheckOutlined /> : <CopyOutlined />}
        onClick={handleCopy}
        style={{ position: 'absolute', top: 6, right: 6, zIndex: 1 }}
        aria-label="复制 SQL"
      >
        {copied ? '已复制' : '复制'}
      </Button>
      {/* SQL 代码块，等宽字体保留格式 */}
      <pre
        style={{
          margin: 0,
          padding: '12px 72px 12px 12px',
          fontFamily:
            "'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, Courier, monospace",
          fontSize: 13,
          lineHeight: 1.5,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          color: '#333',
        }}
      >
        <Text copyable={false}>{sql}</Text>
      </pre>
    </div>
  );
}
