# DDS 双主题切换系统实现方案

**日期**: 2026-05-31  
**目标**: 支持深色/浅色无缝切换，右上角控件，本地存储偏好

---

## 系统架构

### 1. CSS 变量两套主题定义

#### 深色主题（Dark Theme）
```css
:root[data-theme="dark"] {
  /* 背景层级 */
  --paper: #0A0A0C;        /* 页面底 */
  --paper-1: #141419;      /* 卡片 */
  --paper-2: #1A1A22;      /* 浮层 */
  --paper-3: #242429;      /* 强调浮层 */
  
  /* 文字颜色 */
  --ink: #FFFFFF;          /* 主文字 */
  --text-primary: #FFFFFF; /* 优先文字 */
  --muted: #B8B8B8;        /* 次要文字 */
  --muted-light: #8B8B8B;  /* 禁用文字 */
  
  /* 强调色 */
  --yellow: #f0c020;       /* 识别色 */
  --blue: #1040c0;         /* 次要行动 */
  --red: #d02020;          /* 警告/风险 */
  --white: #FFFFFF;
  
  /* 线条与分隔符 */
  --line: rgba(255,255,255,0.12);
  --line-color: rgba(255,255,255,0.12);
  --sector-color: rgba(255,255,255,0.08);
  --border: rgba(255,255,255,0.08);
  
  /* 字体 */
  --font-mono: "Courier New", monospace;
}
```

#### 浅色主题（Light Theme）
```css
:root[data-theme="light"] {
  /* 背景层级 */
  --paper: #FFFFFF;        /* 页面底 */
  --paper-1: #F8F8F8;      /* 卡片 */
  --paper-2: #F0F0F0;      /* 浮层 */
  --paper-3: #E8E8E8;      /* 强调浮层 */
  
  /* 文字颜色 */
  --ink: #1A1A22;          /* 主文字（深灰） */
  --text-primary: #1A1A22; /* 优先文字 */
  --muted: #666666;        /* 次要文字 */
  --muted-light: #999999;  /* 禁用文字 */
  
  /* 强调色 */
  --yellow: #D4A820;       /* 识别色（深金） */
  --blue: #0020A0;         /* 次要行动（深蓝） */
  --red: #A00000;          /* 警告/风险（深红） */
  --white: #000000;
  
  /* 线条与分隔符 */
  --line: rgba(0,0,0,0.08);
  --line-color: rgba(0,0,0,0.08);
  --sector-color: rgba(0,0,0,0.04);
  --border: rgba(0,0,0,0.06);
  
  /* 字体 */
  --font-mono: "Courier New", monospace;
}
```

### 关键差异表

| 元素 | 深色 | 浅色 | 说明 |
|------|------|------|------|
| 背景 | #0A0A0C | #FFFFFF | 反差最大 |
| 文字 | #FFFFFF | #1A1A22 | 高对比 |
| 线条 | rgba(255,255,255,0.12) | rgba(0,0,0,0.08) | 适配背景 |
| 识别色 | #f0c020 | #D4A820 | 浅色更深 |

---

## 2. JavaScript 主题切换逻辑

### 核心模块

```javascript
// ============================================
// 主题管理系统
// ============================================

const ThemeManager = {
  // 初始化主题系统
  init() {
    const stored = localStorage.getItem('dds-theme');
    const prefersLight = window.matchMedia('(prefers-color-scheme: light)').matches;
    const theme = stored || (prefersLight ? 'light' : 'dark');
    this.setTheme(theme);
    this.setupToggleButton();
    this.watchSystemPreference();
  },

  // 设置当前主题
  setTheme(theme) {
    if (!['light', 'dark'].includes(theme)) return;
    
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('dds-theme', theme);
    
    // 更新按钮状态
    const btn = document.getElementById('theme-toggle-btn');
    if (btn) {
      btn.setAttribute('aria-label', theme === 'dark' ? '切换浅色模式' : '切换深色模式');
      btn.classList.toggle('light-mode', theme === 'light');
      btn.setAttribute('data-current-theme', theme);
    }
    
    // 触发自定义事件（用于其他组件监听）
    window.dispatchEvent(new CustomEvent('themechange', { detail: { theme } }));
  },

  // 切换主题
  toggle() {
    const current = document.documentElement.getAttribute('data-theme') || 'dark';
    const next = current === 'dark' ? 'light' : 'dark';
    this.setTheme(next);
  },

  // 获取当前主题
  getCurrent() {
    return document.documentElement.getAttribute('data-theme') || 'dark';
  },

  // 监听系统偏好变化
  watchSystemPreference() {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', (e) => {
      if (!localStorage.getItem('dds-theme')) {
        this.setTheme(e.matches ? 'light' : 'dark');
      }
    });
  },

  // 设置切换按钮
  setupToggleButton() {
    const btn = document.getElementById('theme-toggle-btn');
    if (!btn) return;
    
    btn.addEventListener('click', () => this.toggle());
    
    // 快捷键支持（Ctrl/Cmd + Shift + T）
    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key === 'T') {
        e.preventDefault();
        this.toggle();
      }
    });
  }
};

// 页面加载时初始化
document.addEventListener('DOMContentLoaded', () => ThemeManager.init());
```

---

## 3. 右上角切换控件 HTML

### 放置位置
在 HTML 头部导航栏中，右上角（通常在刷新按钮或其他工具之前）

### 完整 HTML

```html
<!-- 右上角工具栏 -->
<div class="top-toolbar">
  <!-- ... 其他工具按钮 ... -->
  
  <!-- 主题切换按钮 -->
  <button id="theme-toggle-btn" 
          class="theme-toggle-btn" 
          aria-label="切换深色模式"
          title="主题切换 (Ctrl+Shift+T)">
    <svg class="theme-icon dark-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
      <!-- 月亮图标（深色模式） -->
      <path d="M17.293 13.633A8.001 8.001 0 0 1 6.367 2.707a.75.75 0 0 0-1.06 1.06A6.5 6.5 0 1 0 16.233 14.693a.75.75 0 1 0-1.06-1.06h.12z" fill="currentColor"/>
    </svg>
    <svg class="theme-icon light-icon" width="20" height="20" viewBox="0 0 20 20" fill="none">
      <!-- 太阳图标（浅色模式） -->
      <path d="M10 4a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0V5a1 1 0 0 1 1-1zm0 10a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm0 2a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm8.536-1.536a1 1 0 1 0 1.414-1.414l-.707-.707a1 1 0 0 0-1.414 1.414l.707.707zM4.464 4.464a1 1 0 1 0 1.414-1.414L5.171 2.343a1 1 0 0 0-1.414 1.414l.707.707z" fill="currentColor"/>
    </svg>
  </button>
</div>
```

### CSS 样式

```css
/* 右上角工具栏容器 */
.top-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  padding: 8px;
  background: var(--paper-1);
  border-radius: 4px;
  border: 1px solid var(--border);
}

/* 主题切换按钮 */
.theme-toggle-btn {
  position: relative;
  width: 40px;
  height: 40px;
  border: 2px solid var(--border);
  background: var(--paper-2);
  border-radius: 4px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--muted);
  transition: all 0.25s ease-out;
}

.theme-toggle-btn:hover {
  color: var(--ink);
  border-color: var(--yellow);
  background: var(--paper-3);
  box-shadow: 0 0 8px rgba(240, 192, 32, 0.2);
}

.theme-toggle-btn:active {
  transform: scale(0.95);
}

/* 图标容器 */
.theme-icon {
  position: absolute;
  opacity: 1;
  transition: opacity 0.2s ease-out, transform 0.2s ease-out;
}

/* 深色图标（深色模式显示） */
.dark-icon {
  opacity: 1;
  transform: rotate(0deg) scale(1);
}

/* 浅色图标（浅色模式显示） */
.light-icon {
  opacity: 0;
  transform: rotate(-90deg) scale(0.5);
  position: absolute;
}

/* 浅色模式时切换图标 */
.theme-toggle-btn.light-mode .dark-icon {
  opacity: 0;
  transform: rotate(90deg) scale(0.5);
}

.theme-toggle-btn.light-mode .light-icon {
  opacity: 1;
  transform: rotate(0deg) scale(1);
}

/* 焦点指示 */
.theme-toggle-btn:focus-visible {
  outline: 2px solid var(--yellow);
  outline-offset: 2px;
}
```

---

## 4. 关键修改清单

### 需要修改的 CSS 变量引用

为确保所有元素都支持两种主题，需要检查和修改：

#### SVG 画布元素
```css
/* ❌ 硬编码颜色（需修改） */
.dds-svg-grid-line { stroke: rgba(255,255,255,0.08); }

/* ✅ 改为变量 */
.dds-svg-grid-line { stroke: var(--line-color); }
```

#### 卡片与面板
```css
/* 卡片背景与边框 */
.card {
  background: var(--paper-1);
  border: 1px solid var(--border);
  color: var(--ink);
}
```

#### 文字颜色
```css
/* 主标题 */
h1, h2, h3 { color: var(--ink); }

/* 次要文字 */
.label, .caption { color: var(--muted); }

/* 禁用状态 */
.disabled { color: var(--muted-light); }
```

#### 输入框与表单
```css
input, textarea, select {
  background: var(--paper-2);
  color: var(--ink);
  border: 2px solid var(--border);
}

input:focus {
  border-color: var(--yellow);
  box-shadow: 0 0 0 3px rgba(240,192,32,0.1);
}
```

---

## 5. 浅色主题特殊处理

### 透明度调整
浅色背景下需要调整某些透明层：

```css
/* 深色主题：暗色透明背景 */
[data-theme="dark"] .overlay {
  background: rgba(0, 0, 0, 0.7);
}

/* 浅色主题：亮色透明背景 */
[data-theme="light"] .overlay {
  background: rgba(0, 0, 0, 0.15);
}
```

### 阴影调整
```css
/* 深色主题 */
[data-theme="dark"] .card {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.4);
}

/* 浅色主题 */
[data-theme="light"] .card {
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
}
```

---

## 6. 实施步骤

### Step 1: 添加 CSS 变量
- [ ] 在 `:root` 中定义深色主题变量
- [ ] 添加 `[data-theme="light"]` 的浅色主题变量
- [ ] 验证所有颜色都使用变量（无硬编码）

### Step 2: 实现 JavaScript 逻辑
- [ ] 添加 `ThemeManager` 对象
- [ ] 实现 `init()`、`setTheme()`、`toggle()` 方法
- [ ] 支持本地存储和系统偏好检测
- [ ] 在 `DOMContentLoaded` 时初始化

### Step 3: 添加 UI 控件
- [ ] 在右上角添加切换按钮
- [ ] 添加月亮/太阳 SVG 图标
- [ ] 实现图标旋转动画

### Step 4: 样式优化
- [ ] 验证浅色背景下的对比度（WCAG AA+）
- [ ] 调整透明度和阴影
- [ ] 测试所有元素在两种主题下的可见性

### Step 5: 测试验证
- [ ] 刷新页面后主题持久化
- [ ] 快捷键 Ctrl+Shift+T 正常工作
- [ ] 系统偏好自动检测
- [ ] 颜色过渡平滑（无闪烁）

---

## 7. 浏览器兼容性

| 功能 | Chrome | Firefox | Safari | 说明 |
|------|--------|---------|--------|------|
| CSS 变量 | ✅ 49+ | ✅ 31+ | ✅ 9.1+ | 广泛支持 |
| prefers-color-scheme | ✅ 76+ | ✅ 67+ | ✅ 12.1+ | 系统偏好 |
| localStorage | ✅ 4+ | ✅ 2+ | ✅ 4+ | 本地存储 |

---

## 8. 无障碍性 (Accessibility)

### 支持高对比度模式
```javascript
if (window.matchMedia('(prefers-contrast: more)').matches) {
  document.documentElement.setAttribute('data-contrast', 'high');
}
```

### ARIA 标签
```html
<button aria-label="切换深色模式" aria-pressed="false">
  <!-- 图标 -->
</button>
```

### 键盘快捷方式
- **Ctrl+Shift+T**: 切换主题（已实现）
- **Tab**: 聚焦按钮
- **Enter/Space**: 激活切换

---

## 预期效果

✅ **用户点击右上角按钮**
  → 月亮/太阳图标平滑旋转
  → 所有页面颜色立即切换
  → 下次访问时自动恢复选择

✅ **系统偏好自动检测**
  → 首次访问时根据操作系统偏好选择主题
  → 用户手动切换后保存偏好
  → 不再自动跟随系统变化

✅ **完整的颜色方案**
  → 深色：高对比 + Bauhaus 深色美学
  → 浅色：柔和 + 专业浅色美学
  → 所有元素两种主题都清晰可读

---

**总结**: 这套主题系统完全符合 Bauhaus 设计原则（功能性、清晰性、美学），同时提供完美的用户体验。

