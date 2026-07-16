import { useEffect, useState } from 'react';
import AppLayout from './components/AppLayout';
import AdminApp from './components/AdminApp';

/** 根组件：根据路径渲染问数前台或权限管理后台（/admin） */
export default function App() {
  const [path, setPath] = useState(window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener('popstate', onPop);
    return () => window.removeEventListener('popstate', onPop);
  }, []);
  if (path.startsWith('/admin')) return <AdminApp />;
  return <AppLayout />;
}
