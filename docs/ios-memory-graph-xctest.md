# iOS 真机内存图（.memgraph）采集指南

> 面向 Unity 导出 Xcode 工程 + idevice 自动化场景。物理 iOS 真机无法通过 `leaks --outputGraph` 或 debugserver 公开 API 导出与 Xcode「Debug Memory Graph」等价的 `.memgraph` 文件；Apple 官方路径为 **XCTest + XCTMemoryMetric + xcodebuild 性能诊断**。

---

## 目录

1. [背景与方案选型](#背景与方案选型)
2. [核心概念：XCTMemoryMetric 是什么](#核心概念xctmemorymetric-是什么)
3. [XCTest 性能测试配置（逐步）](#xctest-性能测试配置逐步)
4. [xcodebuild 命令行采集](#xcodebuild-命令行采集)
5. [从 xcresult 导出 memgraph](#从-xcresult-导出-memgraph)
6. [Unity 导出 Xcode 工程处理](#unity-导出-xcode-工程处理)
7. [用 HTTP 替代 sleep 等待游戏状态](#用-http-替代-sleep-等待游戏状态)
8. [Unity UI 暴露给 XCTest（Accessibility）](#unity-ui-暴露给-xctestaccessibility)
9. [idevice 集成（计划 API）](#idevice-集成计划-api)
10. [分析导出的 memgraph](#分析导出的-memgraph)
11. [推荐落地流程](#推荐落地流程)
12. [常见问题](#常见问题)
13. [参考链接](#参考链接)

---

## 背景与方案选型

### idevice 现状

| 能力 | 说明 |
|------|------|
| `IOSDevice3` DVT | 已有 `launch` / `pkill` / `proclist` |
| pymobiledevice3 `sysmon` | 可采集 `physFootprint` 等指标，**不产出** `.memgraph` |
| `leaks --outputGraph` | **仅本机进程**（含 iOS 模拟器），**物理真机不可用** |
| debugserver + LLDB | 可 attach，但 LLDB 无等价 `export memgraph` 命令 |

### 为何选用 XCTest

Apple WWDC21「Detect and diagnose memory issues」说明：

- 在 `measure(metrics: [XCTMemoryMetric(...)])` 的性能测试中
- 配合 `xcodebuild test -enablePerformanceTestsDiagnostics YES`
- Xcode 会在 `.xcresult` 中附加 **pre / post** 两份 `.memgraph`
- **真机**上可靠；模拟器上 memgraph 诊断不可靠

---

## 核心概念：XCTMemoryMetric 是什么

**「调用方必须已有带 XCTMemoryMetric 的 UI/性能测试」** 的含义：

- idevice **不会**替你写测试代码
- idevice **不会**自动开始采集内存图
- 你的 Xcode 工程里必须已经有一个性能测试，并在 `measure(...)` 中使用 `XCTMemoryMetric`

### 角色分工

| 角色 | 负责内容 |
|------|----------|
| 你的 XCTest | 定义「测什么场景、何时开始/结束测量」 |
| idevice / xcodebuild | 在指定 UDID 上跑测试，从 `.xcresult` 导出 `.memgraph` |

### 每次 measure 产出什么

XCTest 为启用 malloc stack logging 会多跑一轮 warmup，因此通常有 **2 个 memgraph**：

| 文件前缀 | 含义 |
|----------|------|
| `pre_*.memgraph` | 测量迭代开始前快照 |
| `post_*.memgraph` | 测量迭代结束后快照 |

可用 `heap -diffFrom` 等工具对比 pre/post 分析内存增长。

---

## XCTest 性能测试配置（逐步）

> **自动化**：步骤 1-3（添加 UI Testing Bundle target、编写性能测试、配置 Scheme）可由 idevice 的 `XCDevice.upsert_testing_bundle()` 自动完成，无需在 Xcode 中手动操作：
>
> ```python
> from idevice.device import XCDevice
>
> xc = XCDevice("/path/to/UnityExport")  # 或 .xcodeproj 路径
> xc.upsert_testing_bundle(
>     bundle_name="UnityMemoryUITests",
>     test_class="UnityMemoryUITests",
>     test_method="testGameplayMemory",
>     # app_target / scheme 默认从工程的 application target 推断
> )
> ```
>
> 该方法是**幂等**的（重复调用返回 `False`，不会重复插入），适合放进 Unity 重导出后的后处理流程（见 [Unity 重导出注意点](#unity-重导出-xcode-的注意点)）。下面仍保留手动步骤说明，便于理解其修改内容。**步骤 4（本地验证）仍需手动在真机执行。**

### 步骤 1：添加 UI Test Target

1. 打开 `Unity-iPhone.xcodeproj`（或你的主工程）
2. `File → New → Target`
3. 选择 **UI Testing Bundle**
4. 命名，例如：`UnityMemoryUITests`
5. 确认 `TEST_TARGET_NAME` 指向主 App target（`Unity-iPhone`）

### 步骤 2：编写性能测试

在 `UnityMemoryUITests` 中新建测试方法：

```swift
import XCTest

final class UnityMemoryUITests: XCTestCase {

    func testGameplayMemory() throws {
        let app = XCUIApplication()
        let options = XCTMeasureOptions()
        options.invocationOptions = [.manuallyStart]

        measure(
            metrics: [XCTMemoryMetric(application: app)],
            options: options
        ) {
            app.launch()

            // 等待游戏就绪（见下文 HTTP / Accessibility 方案）
            // try waitUntilGameReady(timeout: 60)

            startMeasuring()

            // 在此执行要测内存的操作
            // 例如：进战斗、切 UI、重进关卡
            sleep(30)

            XCTAssertTrue(app.state == .runningForeground)
        }
    }
}
```

### 步骤 3：配置 Scheme

1. 选中主 App Scheme（如 `Unity-iPhone`）
2. `Product → Scheme → Edit Scheme`
3. 左侧选 **Test**，勾选 `UnityMemoryUITests`
4. （可选）**Diagnostics** 勾选 **Malloc Stack Logging → Live Allocations**
   - 有助于 memgraph 中带分配栈
5. （可选）**Test** 中取消「Delete attachments when test succeeds」，便于调试

### 步骤 4：本地验证

1. 真机 USB 连接，Developer Mode 已开启
2. `Product → Test`（或只跑单个测试）
3. 在 Report Navigator 中打开本次 `.xcresult`
4. 展开测试日志，确认底部有 **memgraph 附件**

若本地无 memgraph，先不要接 idevice，先修测试/Scheme 配置。

---

## xcodebuild 命令行采集

### 基本命令

```bash
xcodebuild test \
  -project /path/to/Unity-iPhone.xcodeproj \
  -scheme Unity-iPhone \
  -destination 'platform=iOS,id=00008101-00161DAE14B8001E' \
  -only-testing:UnityMemoryUITests/testGameplayMemory \
  -enablePerformanceTestsDiagnostics YES \
  -resultBundlePath ./TestResults.xcresult
```

使用 workspace 时改为 `-workspace Your.xcworkspace`。

### 关键参数说明

| 参数 | 说明 |
|------|------|
| `-destination 'platform=iOS,id=<UDID>'` | 指定真机 UDID |
| `-only-testing:<Target>/<Method>` | 只跑单个性能测试 |
| `-enablePerformanceTestsDiagnostics YES` | **必须**，否则不生成 memgraph |
| `-resultBundlePath` | 输出 `.xcresult` 路径 |

### 环境变量（可选）

```bash
export IDEVICE_XCODEBUILD_BINARY=xcodebuild  # idevice 计划支持
```

---

## 从 xcresult 导出 memgraph

### 导出全部附件

```bash
xcrun xcresulttool export attachments \
  --path ./TestResults.xcresult \
  --output-path ./memgraph_output
```

会在 `memgraph_output` 生成：

- 若干 `*.memgraph` 文件
- `manifest.json`（附件与测试用例的映射）

### 仅导出某个测试的附件

```bash
xcrun xcresulttool export attachments \
  --path ./TestResults.xcresult \
  --output-path ./memgraph_output \
  --test-id UnityMemoryUITests/testGameplayMemory
```

### 用 Xcode 查看

双击 `.xcresult` 或在 Xcode 中 `File → Open` 打开，在测试报告里查看 memgraph 附件。

---

## Unity 导出 Xcode 工程处理

### 典型目录结构

```
YourUnityExport/
├── Unity-iPhone.xcodeproj
├── Unity-iPhone/          # 主 App（被测进程）
├── UnityFramework/
├── Libraries/
└── ...
```

| Target | 作用 |
|--------|------|
| `Unity-iPhone` | 主 App，内存图针对此进程 |
| `UnityFramework` | Unity 运行时 |

Bundle ID 在 Unity：`Edit → Project Settings → Player → iOS → Bundle Identifier`。

### 针对 Unity 的测试模板（场景内存）

```swift
func testLevelMemory() throws {
    let app = XCUIApplication()
    // 通过启动参数直达场景（需 Unity 侧解析）
    app.launchArguments += ["-unity-test-scene", "Level_01"]

    let options = XCTMeasureOptions()
    options.invocationOptions = [.manuallyStart]

    measure(metrics: [XCTMemoryMetric(application: app)], options: options) {
        app.launch()

        // 等 Unity 场景加载
        sleep(15)

        startMeasuring()

        // 测 60 秒场景内操作
        sleep(60)
    }
}
```

### Unity 重导出 Xcode 的注意点

Unity **重新 Build** 可能覆盖或丢失手动添加的 UI Test target。

建议：

1. **PostProcessBuild** 脚本在每次导出后自动添加 Test target
2. 将 Test target 放在 **独立 workspace**，与 Unity 导出目录并列
3. 文档化「每次 Unity 导出后需重新检查 Test target」

### idevice XCDevice（工程准备）

仓库中已有 `XCDevice` 用于 Unity 导出工程的 prepare/build（与内存测试无直接关系，但常用于先编译出 `.app`）：

```python
from idevice.device import XCDevice

xc = XCDevice("/path/to/UnityExport")  # 或 .xcodeproj 路径
xc.prepare()   # 清理 IAP 等 capability
app_path = xc.build()
```

---

## 用 HTTP 替代 sleep 等待游戏状态

`sleep(10)` 是盲等；用 HTTP 轮询可在游戏 **真正 ready** 后再 `startMeasuring()`，测试更稳、更快。

### 流程

```text
XCTest (Swift)
   ↓ HTTP GET
Unity App 内置 HTTP Server
   ↓ JSON
{"ready": true, "scene": "MainMenu"}
```

### Swift：轮询辅助函数

```swift
extension URLSession {
    func syncData(from url: URL) throws -> (Data, URLResponse) {
        var result: (Data, URLResponse)?
        var error: Error?
        let sem = DispatchSemaphore(value: 0)

        let task = dataTask(with: url) { data, response, err in
            if let err = err { error = err }
            else if let data = data, let response = response {
                result = (data, response)
            }
            sem.signal()
        }
        task.resume()
        sem.wait()

        if let error = error { throw error }
        guard let result = result else { throw URLError(.badServerResponse) }
        return result
    }
}

func waitUntilGameReady(timeout: TimeInterval = 60) throws {
    let deadline = Date().addingTimeInterval(timeout)
    let url = URL(string: "http://127.0.0.1:17890/status")!

    while Date() < deadline {
        let (data, response) = try URLSession.shared.syncData(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            Thread.sleep(forTimeInterval: 0.5)
            continue
        }

        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        if json?["ready"] as? Bool == true,
           json?["scene"] as? String == "MainMenu" {
            return
        }

        Thread.sleep(forTimeInterval: 0.5)
    }

    XCTFail("Game not ready within \(timeout)s")
}

func waitUntilState(_ scene: String, timeout: TimeInterval = 60) throws {
    let deadline = Date().addingTimeInterval(timeout)
    let url = URL(string: "http://127.0.0.1:17890/status")!

    while Date() < deadline {
        let (data, response) = try URLSession.shared.syncData(from: url)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            Thread.sleep(forTimeInterval: 0.5)
            continue
        }
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        if json?["scene"] as? String == scene { return }
        Thread.sleep(forTimeInterval: 0.5)
    }
    XCTFail("Scene \(scene) not reached within \(timeout)s")
}
```

### Swift：与内存测试配合

```swift
measure(metrics: [XCTMemoryMetric(application: app)], options: options) {
    app.launch()
    try waitUntilGameReady(timeout: 60)   // 替代 sleep(10)

    startMeasuring()

    app.buttons["btn_battle"].tap()       // 或 HTTP 触发
    try waitUntilState("InBattle", timeout: 30)
}
```

### Unity：暴露状态接口（思路）

```csharp
// GET /status 返回示例
// {"ready": true, "scene": "MainMenu", "loading": false}

public class TestStatusServer : MonoBehaviour
{
    void Update()
    {
        GameState.Ready = !LoadingManager.IsLoading
            && SceneManager.GetActiveScene().name == "MainMenu";
        GameState.Scene = SceneManager.GetActiveScene().name;
    }
}
```

仅在 `#if UNITY_IOS && !UNITY_EDITOR` 且测试构建中启用 HTTP 服务。

### 真机网络注意

| 环境 | 访问地址 | 说明 |
|------|----------|------|
| 模拟器 | `http://127.0.0.1:17890` | XCTest 与 App 同机，localhost 通常可用 |
| 真机 | `http://127.0.0.1:17890` | 部分环境可行，**需 POC 验证** |
| 真机备选 | `http://<device_ip>:17890` | 游戏监听 `0.0.0.0`，XCTest 访问设备 IP |

### 方式对比

| 方式 | 优点 | 缺点 |
|------|------|------|
| `sleep` | 实现简单 | 慢、不稳定 |
| HTTP 轮询 | 精确表达游戏状态 | 需游戏内 HTTP；真机地址需验证 |
| Accessibility 等待 | 真机最稳、XCTest 原生 | Unity 需暴露 accessibility |
| `launchArguments` | 跳过加载等待 | 需改 Unity 启动逻辑 |

**推荐组合**：`launchArguments` 直达场景 + HTTP 或 Accessibility 确认加载完成 + `startMeasuring()`。

---

## Unity UI 暴露给 XCTest（Accessibility）

XCTest 查询的是 iOS **Accessibility 树**，不是 Unity `GameObject.name`。

```swift
app.buttons["StartGame"].tap()
app.staticTexts["MainMenuReady"].waitForExistence(timeout: 60)
```

对应 iOS 原生属性：

| 属性 | 作用 |
|------|------|
| `accessibilityLabel` | 可读名称，XCTest 常用查询键 |
| `accessibilityIdentifier` | 稳定 ID，**自动化推荐** |
| `isAccessibilityElement` | 是否暴露给系统 |
| `accessibilityTraits` | Button / StaticText 等类型 |

Unity 默认 **不会** 完整暴露 uGUI 到 iOS Accessibility，需手动桥接。

### 方案 A：Unity 6 官方 Accessibility 模块（2023.2+ / Unity 6）

```csharp
using UnityEngine;
using UnityEngine.Accessibility;
using UnityEngine.UI;

public class AccessibleButton : MonoBehaviour
{
    private Button _button;
    private AccessibilityHierarchy _hierarchy;
    private AccessibilityNode _node;

    void Start()
    {
        _button = GetComponent<Button>();
        var label = GetComponentInChildren<Text>()?.text ?? "StartGame";

        _hierarchy = new AccessibilityHierarchy();
        _node = _hierarchy.AddNode(label);
        _node.role = AccessibilityRole.Button;
        _node.label = label;

        _node.invoked += () => _button.onClick.Invoke();

        AssistiveSupport.activeHierarchy = _hierarchy;
    }

    void OnDestroy()
    {
        if (AssistiveSupport.activeHierarchy == _hierarchy)
            AssistiveSupport.activeHierarchy = null;
    }
}
```

文档：[Get started with screen reader support](https://docs.unity3d.com/6000.4/Documentation/Manual/accessibility/screen-readers-get-started.html)

**注意**：社区反馈 iOS 上 Accessibility 层级可能仅在 **VoiceOver 开启** 时才完全生效，真机 XCTest 需实测。

### 方案 B：测试专用状态标记（推荐）

不必暴露全部 UI，只暴露少量测试标记：

**Unity（C#，通过原生插件或 Accessibility 模块）：**

```csharp
// 主界面就绪时
IOSAccessibility.SetStaticText("test_state", "MainMenuReady");

// 进入战斗时
IOSAccessibility.SetStaticText("test_state", "InBattle");
```

**XCTest：**

```swift
XCTAssertTrue(app.staticTexts["MainMenuReady"].waitForExistence(timeout: 60))
startMeasuring()

app.buttons["btn_battle"].tap()
XCTAssertTrue(app.staticTexts["InBattle"].waitForExistence(timeout: 30))
```

可配合不可见空 GameObject，仅更新 accessibility 文本，画面上无 UI。

### 方案 C：原生 UIAccessibility 插件（全 Unity 版本）

通用桥接组件示例：

```csharp
public class AccessibilityBridge : MonoBehaviour
{
    [SerializeField] private string accessibilityId;    // "btn_battle"
    [SerializeField] private string accessibilityLabel; // "EnterBattle"
    [SerializeField] private bool isButton = true;

    void Start()
    {
#if UNITY_IOS && !UNITY_EDITOR
        IOSAccessibility.SetElement(
            gameObject.GetInstanceID(),
            accessibilityId,
            accessibilityLabel,
            isButton
        );
#endif
    }

    void OnDestroy()
    {
#if UNITY_IOS && !UNITY_EDITOR
        IOSAccessibility.RemoveElement(gameObject.GetInstanceID());
#endif
    }
}
```

iOS 原生侧（`IOSAccessibility.mm`）需设置：

- `accessibilityIdentifier`
- `accessibilityLabel`
- `accessibilityTraits`（Button / StaticText）
- `accessibilityFrame`（从 Unity `RectTransform` 转屏幕坐标，注意 Y 轴翻转）

**XCTest 查询：**

```swift
app.buttons["btn_battle"].tap()           // identifier
app.staticTexts["MainMenuReady"].exists   // label
```

### 验证 Accessibility 是否生效

1. 真机运行游戏
2. `Settings → Accessibility → VoiceOver` 开启，或 Xcode **Accessibility Inspector** 连接设备
3. 确认能读到 `StartGame`、`MainMenuReady` 等
4. 能读到则 XCTest 通常也能查到

### 方案对比

| 方案 | 适用版本 | 优点 | 缺点 |
|------|----------|------|------|
| Unity Accessibility 模块 | Unity 6 / 2023.2+ | 官方、跨平台 | iOS 可能依赖 VoiceOver |
| 原生 UIAccessibility 插件 | 全版本 | 可控、适合 XCTest | 需维护 iOS 插件 |
| 测试标记 staticText | 全版本 | 简单稳定 | 只能暴露少量状态 |
| HTTP 轮询 | 全版本 | 不依赖 accessibility | 需 HTTP 服务 |

---

## idevice 集成（计划 API）

> **状态**：`XCDevice.upsert_testing_bundle()`（步骤 1-3 自动化）已实现，见上文 [XCTest 性能测试配置（逐步）](#xctest-性能测试配置逐步)。`capture_memory_graph`（步骤 7-8 采集/导出）尚在 `xctest` 分支计划中，以下为目标 API 形态。

### 已实现：自动生成 UI Testing Bundle

```python
from idevice.device import XCDevice

xc = XCDevice("/path/to/UnityExport")
changed = xc.upsert_testing_bundle()  # 步骤 1-3；True=有修改，可幂等重跑
```

完成后即可用 `xcodebuild test -enablePerformanceTestsDiagnostics YES` 或下面的计划 API 采集 memgraph。

### Python 调用示例

```python
from pathlib import Path
from idevice.device import Device, Platform

device = Device.create(Platform.IOS3, device_id="00008101-...", device_ip="")

result = device.capture_memory_graph(
    project=Path("Unity-iPhone.xcodeproj"),
    scheme="Unity-iPhone",
    test_filter="UnityMemoryUITests/testGameplayMemory",
    result_bundle=Path("./TestResults.xcresult"),  # 可选
    output_dir=Path("./memgraph_output"),          # 可选
)

for mg in result.memgraphs:
    print(mg.path, mg.name, mg.test_identifier)
```

### 计划返回结构

```python
@dataclass(frozen=True)
class MemoryGraphFile:
    path: Path
    name: str
    test_identifier: str

@dataclass(frozen=True)
class MemoryGraphCaptureResult:
    xcresult_path: Path
    memgraphs: list[MemoryGraphFile]
    manifest_path: Path | None
    xcodebuild_returncode: int
```

### 前提条件

- macOS + Xcode 已安装
- 工程内已有 `XCTMemoryMetric` 性能测试
- 真机 USB 连接、已配对、Developer Mode 开启
- idevice 负责 `xcodebuild` + `xcresulttool export attachments`，**不生成** Swift 测试代码

---

## 分析导出的 memgraph

导出后可用 Apple 命令行工具分析（在 Mac 上）：

```bash
# 泄漏检测
leaks --fullContent YourApp.memgraph

# 虚拟内存区域摘要
vmmap --summary YourApp.memgraph

# 堆对象按大小排序
heap YourApp.memgraph -sortBySize

# 对比 pre/post 增长
heap -diffFrom pre_xxx.memgraph post_xxx.memgraph

# 某地址分配栈
malloc_history YourApp.memgraph 0x<address>
```

也可用 Xcode 打开 `.memgraph`：`File → Open` 或双击文件。

---

## 推荐落地流程

```text
1. Unity 导出 Xcode 工程
       ↓
2. Xcode 添加 UnityMemoryUITests（UI Testing Bundle）
       ↓
3. 编写 testGameplayMemory（XCTMemoryMetric + measure）
       ↓
4. （可选）Unity 暴露 HTTP /status 或 Accessibility 标记
       ↓
5. Edit Scheme → Test 勾选测试 target；Diagnostics 勾选 Malloc Stack
       ↓
6. 真机 Product → Test，确认 xcresult 含 memgraph
       ↓
7. xcodebuild -enablePerformanceTestsDiagnostics YES（或 idevice API）
       ↓
8. xcresulttool export attachments → *.memgraph
       ↓
9. leaks / heap / vmmap 分析
```

---

## 常见问题

### Q：测试通过了但没有 memgraph？

- 确认测试使用了 `XCTMemoryMetric`，不是普通 UI 测试
- 确认命令行加了 `-enablePerformanceTestsDiagnostics YES`
- 确认在 **真机** 上跑（模拟器不可靠）
- 检查 Scheme → Test → 附件是否被「成功后删除」

### Q：「调用方必须已有 XCTMemoryMetric 测试」是什么意思？

idevice 只负责 **跑你写好的性能测试并导出附件**；测试代码必须由你在 Xcode 工程中预先编写。

### Q：Unity 按钮 XCTest 点不到？

Unity uGUI 默认不在 Accessibility 树中。使用本文 [Accessibility 暴露方案](#unity-ui-暴露给-xctestaccessibility)，或改用 HTTP / `launchArguments` 驱动场景。

### Q：真机 HTTP 127.0.0.1 不通？

尝试设备 IP + 游戏监听 `0.0.0.0`，或改用 Accessibility 等待（真机更稳）。

### Q：Unity 重新导出后测试 target 丢了？

见 [Unity 重导出注意点](#unity-重导出-xcode-的注意点)；建议 PostProcessBuild 或独立 workspace。

---

## 参考链接

- [Gathering information about memory use](https://developer.apple.com/documentation/xcode/gathering-information-about-memory-use)
- WWDC21：Detect and diagnose memory issues（`enablePerformanceTestsDiagnostics`）
- [Unity Accessibility 入门](https://docs.unity3d.com/6000.4/Documentation/Manual/accessibility/screen-readers-get-started.html)
- [xcresulttool export attachments](https://keith.github.io/xcode-man-pages/xcresulttool.1.html)
- 相关笔记：[nzcv/note#52](https://github.com/nzcv/note/issues/52)
