from idevice.device import XCDevice

xc = XCDevice("/Volumes/User/EndlessRunner/XPrj")  # 或 .xcodeproj 路径
xc.prepare()  # 返回 True 表示有修改

# 步骤 1-3：添加 UI Testing Bundle target、写入 XCTMemoryMetric 性能测试、配置 Scheme。
# 返回 True 表示有创建/修改，可重复调用（幂等）。
xc.upsert_testing_bundle(
    bundle_name="UnityMemoryUITests",
    test_class="UnityMemoryUITests",
    test_method="testGameplayMemory",
)

app_path = xc.build()

print(app_path)  # 打印编译后的应用路径

# 方案一：独立、固定的 XCTest/XCUITest 工程，与频繁重导出的 App 工程解耦。
# 测试用例通过 bundle id 启动已安装的 App：
#   let app = XCUIApplication(bundleIdentifier: "com.example.app")
#   app.launch()
# 链路：prepare -> build -> install（用 IOSDevice/IOSDevice3 安装 app_path）-> test。
# Unity 怎么重导出都不影响这个固定测试工程，维护成本最低。
# 这里直接复用仓库自带的模板工程 examples/UITestsTemplate/（见其 README）。
result_bundle = xc.test(
    test_project="examples/UITestsTemplate/UITestsTemplate.xcodeproj",  # 固定测试工程模板
    scheme="UITests",
    udid="00008101-00161DAE14B8001E",  # 目标真机 UDID（也可用 destination=... 自定义）
    # only_testing=["UITests/GameplayUITests/testHappyPath"],  # 可选：只跑指定用例
)
print(result_bundle)  # 打印 .xcresult 结果包路径

# from idevice.device import Device
# xc = Device.create("xc", device_id="/path/to/Unity-iPhone", device_ip="")
# xc.prepare()
# xc.upsert_testing_bundle()
