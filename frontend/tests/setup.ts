/** Vitest 测试环境 setup（决策 D10）*/
import '@testing-library/jest-dom';

// jsdom 缺失 window.matchMedia，AntD responsiveObserver（Descriptions/Table/Collapse 等）依赖它
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// jsdom 缺失 ResizeObserver，react-resizable-panels 依赖它
class ResizeObserverMock {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(window, 'ResizeObserver', {
  writable: true,
  value: ResizeObserverMock,
});
