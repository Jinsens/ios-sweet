#import <UIKit/UIKit.h>
#import <QuartzCore/QuartzCore.h>

#import "SweetGPUEngine.h"

static UIColor *GBColor(CGFloat r, CGFloat g, CGFloat b, CGFloat a) {
    return [UIColor colorWithRed:r green:g blue:b alpha:a];
}

@interface GPUChartView : UIView
@property(nonatomic, copy) NSArray<NSNumber *> *values;
@property(nonatomic) CGFloat maximum;
@end

@implementation GPUChartView

- (instancetype)initWithFrame:(CGRect)frame {
    if ((self = [super initWithFrame:frame])) {
        self.backgroundColor = [UIColor clearColor];
        self.maximum = 120.0;
        self.accessibilityLabel = @"GPU 完成帧率曲线";
    }
    return self;
}

- (void)setValues:(NSArray<NSNumber *> *)values {
    _values = [values copy];
    [self setNeedsDisplay];
}

- (void)drawRect:(CGRect)rect {
    CGContextRef c = UIGraphicsGetCurrentContext();
    CGFloat w = CGRectGetWidth(rect), h = CGRectGetHeight(rect);
    CGFloat left = 30.0, right = 8.0, top = 9.0, bottom = 19.0;
    CGRect plot = CGRectMake(left, top, MAX(0, w - left - right), MAX(0, h - top - bottom));
    CGContextSetStrokeColorWithColor(c, GBColor(1, 1, 1, 0.09).CGColor);
    CGContextSetLineWidth(c, 1.0);
    for (NSUInteger i = 0; i <= 4; i++) {
        CGFloat y = CGRectGetMinY(plot) + CGRectGetHeight(plot) * (CGFloat)i / 4.0;
        CGContextMoveToPoint(c, CGRectGetMinX(plot), y);
        CGContextAddLineToPoint(c, CGRectGetMaxX(plot), y);
    }
    CGContextStrokePath(c);

    NSDictionary *labelAttributes = @{
        NSFontAttributeName: [UIFont monospacedSystemFontOfSize:9 weight:UIFontWeightRegular],
        NSForegroundColorAttributeName: GBColor(1, 1, 1, 0.42)
    };
    for (NSUInteger i = 0; i <= 4; i++) {
        CGFloat value = self.maximum * (CGFloat)(4 - i) / 4.0;
        NSString *text = [NSString stringWithFormat:@"%.0f", value];
        [text drawAtPoint:CGPointMake(0, CGRectGetMinY(plot) + CGRectGetHeight(plot) * (CGFloat)i / 4.0 - 6)
            withAttributes:labelAttributes];
    }

    if (self.values.count < 1 || CGRectGetWidth(plot) <= 0 || CGRectGetHeight(plot) <= 0) {
        return;
    }
    UIBezierPath *line = [UIBezierPath bezierPath];
    NSUInteger count = self.values.count;
    for (NSUInteger i = 0; i < count; i++) {
        CGFloat x = CGRectGetMinX(plot) + (count == 1 ? CGRectGetWidth(plot) :
            CGRectGetWidth(plot) * (CGFloat)i / (CGFloat)(count - 1));
        CGFloat value = MIN(self.maximum, MAX(0, self.values[i].doubleValue));
        CGFloat y = CGRectGetMaxY(plot) - CGRectGetHeight(plot) * value / self.maximum;
        if (i == 0) [line moveToPoint:CGPointMake(x, y)];
        else [line addLineToPoint:CGPointMake(x, y)];
    }
    line.lineWidth = 2.0;
    line.lineJoinStyle = kCGLineJoinRound;
    line.lineCapStyle = kCGLineCapRound;
    [GBColor(0.22, 0.79, 1.0, 1.0) setStroke];
    [line stroke];
}

@end

@interface GPUBenchViewController : UIViewController
@end

@interface GPUBenchViewController ()
@property(nonatomic, strong) SweetGPUEngine *engine;
@property(nonatomic, strong) UISegmentedControl *fpsControl;
@property(nonatomic, strong) UISlider *loadSlider;
@property(nonatomic, strong) UILabel *loadLabel;
@property(nonatomic, strong) UIButton *runButton;
@property(nonatomic, strong) UIButton *exportButton;
@property(nonatomic, strong) UILabel *gpuFPSLabel;
@property(nonatomic, strong) UILabel *avgLabel;
@property(nonatomic, strong) UILabel *p95Label;
@property(nonatomic, strong) UILabel *rendererLabel;
@property(nonatomic, strong) UILabel *elapsedLabel;
@property(nonatomic, strong) UILabel *missedLabel;
@property(nonatomic, strong) UILabel *displayLabel;
@property(nonatomic, strong) UILabel *bannerLabel;
@property(nonatomic, strong) GPUChartView *chartView;
@property(nonatomic, strong) CADisplayLink *displayLink;
@property(nonatomic, strong) NSTimer *stopTimer;
@property(nonatomic, strong) NSDictionary *latestStats;
@property(nonatomic, strong) NSMutableArray<NSNumber *> *chartValues;
@property(nonatomic, strong) NSMutableArray<NSDictionary *> *samples;
@property(nonatomic) NSUInteger displaySamples;
@property(nonatomic) CFTimeInterval displayIntervalSum;
@property(nonatomic) CFTimeInterval displayLastTimestamp;
@property(nonatomic) double displayEstimateHz;
@property(nonatomic) BOOL testing;
@property(nonatomic) NSUInteger sessionGeneration;
@property(nonatomic) BOOL pausedForBackground;
@end

static double GBNumber(NSDictionary *stats, NSString *key) {
    id value = stats[key];
    return [value respondsToSelector:@selector(doubleValue)] ? [value doubleValue] : 0.0;
}

static NSString *GBString(NSDictionary *stats, NSString *key) {
    id value = stats[key];
    return [value isKindOfClass:[NSString class]] ? value : @"—";
}

@implementation GPUBenchViewController

- (void)viewDidLoad {
    [super viewDidLoad];
    self.view.backgroundColor = GBColor(0.045, 0.055, 0.075, 1);
    self.engine = [SweetGPUEngine new];
    self.chartValues = [NSMutableArray array];
    self.samples = [NSMutableArray array];
    [self buildUI];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(appDidEnterBackground)
        name:UIApplicationDidEnterBackgroundNotification object:nil];
    [[NSNotificationCenter defaultCenter] addObserver:self selector:@selector(appWillEnterForeground)
        name:UIApplicationWillEnterForegroundNotification object:nil];
}

- (void)dealloc {
    [[NSNotificationCenter defaultCenter] removeObserver:self];
    [self.displayLink invalidate];
    [self.stopTimer invalidate];
    [self.engine stop];
}

- (UILabel *)label:(NSString *)text size:(CGFloat)size color:(UIColor *)color weight:(UIFontWeight)weight {
    UILabel *label = [UILabel new];
    label.text = text;
    label.textColor = color;
    label.font = [UIFont systemFontOfSize:size weight:weight];
    label.translatesAutoresizingMaskIntoConstraints = NO;
    return label;
}

- (UIView *)card {
    UIView *view = [UIView new];
    view.translatesAutoresizingMaskIntoConstraints = NO;
    view.backgroundColor = GBColor(0.095, 0.11, 0.145, 1);
    view.layer.cornerRadius = 14;
    view.layer.borderColor = GBColor(1, 1, 1, 0.07).CGColor;
    view.layer.borderWidth = 1;
    return view;
}

- (void)buildUI {
    self.title = @"GPU 基准测试";
    UIScrollView *scroll = [UIScrollView new];
    scroll.translatesAutoresizingMaskIntoConstraints = NO;
    [self.view addSubview:scroll];
    [NSLayoutConstraint activateConstraints:@[
        [scroll.topAnchor constraintEqualToAnchor:self.view.safeAreaLayoutGuide.topAnchor],
        [scroll.bottomAnchor constraintEqualToAnchor:self.view.bottomAnchor],
        [scroll.leadingAnchor constraintEqualToAnchor:self.view.leadingAnchor],
        [scroll.trailingAnchor constraintEqualToAnchor:self.view.trailingAnchor]
    ]];
    UIView *content = [UIView new];
    content.translatesAutoresizingMaskIntoConstraints = NO;
    [scroll addSubview:content];
    [NSLayoutConstraint activateConstraints:@[
        [content.topAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.topAnchor constant:18],
        [content.bottomAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.bottomAnchor constant:-28],
        [content.leadingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.leadingAnchor constant:18],
        [content.trailingAnchor constraintEqualToAnchor:scroll.contentLayoutGuide.trailingAnchor constant:-18],
        [content.widthAnchor constraintEqualToAnchor:scroll.frameLayoutGuide.widthAnchor constant:-36]
    ]];

    UILabel *subtitle = [self label:@"测量真实 GPU 离屏提交完成，不代表屏幕刷新率" size:13
        color:GBColor(0.65, 0.7, 0.8, 1) weight:UIFontWeightRegular];
    [content addSubview:subtitle];
    UILabel *targetTitle = [self label:@"GPU 目标帧率" size:13 color:GBColor(1,1,1,0.65) weight:UIFontWeightSemibold];
    [content addSubview:targetTitle];
    self.fpsControl = [[UISegmentedControl alloc] initWithItems:@[@"60 FPS", @"120 FPS"]];
    self.fpsControl.selectedSegmentIndex = 0;
    self.fpsControl.translatesAutoresizingMaskIntoConstraints = NO;
    [content addSubview:self.fpsControl];

    self.loadLabel = [self label:@"负载等级 4 / 8" size:13 color:GBColor(1,1,1,0.65) weight:UIFontWeightSemibold];
    [content addSubview:self.loadLabel];
    self.loadSlider = [UISlider new];
    self.loadSlider.translatesAutoresizingMaskIntoConstraints = NO;
    self.loadSlider.minimumValue = 1; self.loadSlider.maximumValue = 8; self.loadSlider.value = 4;
    self.loadSlider.minimumTrackTintColor = GBColor(0.22,0.79,1,1);
    [self.loadSlider addTarget:self action:@selector(loadChanged:) forControlEvents:UIControlEventValueChanged];
    [content addSubview:self.loadSlider];

    self.runButton = [UIButton buttonWithType:UIButtonTypeSystem];
    self.runButton.translatesAutoresizingMaskIntoConstraints = NO;
    self.runButton.titleLabel.font = [UIFont systemFontOfSize:16 weight:UIFontWeightBold];
    self.runButton.backgroundColor = GBColor(0.12, 0.56, 0.82, 1);
    self.runButton.tintColor = UIColor.whiteColor;
    self.runButton.layer.cornerRadius = 11;
    [self.runButton setTitle:@"开始测试" forState:UIControlStateNormal];
    [self.runButton addTarget:self action:@selector(toggleRun) forControlEvents:UIControlEventTouchUpInside];
    [content addSubview:self.runButton];
    self.exportButton = [UIButton buttonWithType:UIButtonTypeSystem];
    self.exportButton.translatesAutoresizingMaskIntoConstraints = NO;
    self.exportButton.tintColor = GBColor(0.45,0.84,1,1);
    [self.exportButton setTitle:@"导出 JSON" forState:UIControlStateNormal];
    [self.exportButton addTarget:self action:@selector(exportJSON) forControlEvents:UIControlEventTouchUpInside];
    [content addSubview:self.exportButton];

    UIView *metrics = [self card]; [content addSubview:metrics];
    UILabel *gpuCaption = [self label:@"GPU 完成帧率" size:13 color:GBColor(1,1,1,0.58) weight:UIFontWeightSemibold];
    [metrics addSubview:gpuCaption];
    self.gpuFPSLabel = [self label:@"—" size:38 color:GBColor(0.28,0.83,1,1) weight:UIFontWeightBold];
    [metrics addSubview:self.gpuFPSLabel];
    self.avgLabel = [self label:@"平均完成耗时  —" size:14 color:UIColor.whiteColor weight:UIFontWeightMedium]; [metrics addSubview:self.avgLabel];
    self.p95Label = [self label:@"P95 完成耗时  —" size:14 color:UIColor.whiteColor weight:UIFontWeightMedium]; [metrics addSubview:self.p95Label];
    self.rendererLabel = [self label:@"渲染器  —" size:13 color:GBColor(1,1,1,0.68) weight:UIFontWeightRegular]; self.rendererLabel.numberOfLines = 2; [metrics addSubview:self.rendererLabel];
    self.elapsedLabel = [self label:@"已完成  0 帧 · 0.0 s" size:13 color:GBColor(1,1,1,0.68) weight:UIFontWeightRegular]; [metrics addSubview:self.elapsedLabel];
    self.missedLabel = [self label:@"错过目标节拍  —" size:13 color:GBColor(1,1,1,0.68) weight:UIFontWeightRegular]; [metrics addSubview:self.missedLabel];

    UIView *chartCard = [self card]; [content addSubview:chartCard];
    UILabel *chartTitle = [self label:@"GPU 完成帧率（最近样本）" size:13 color:GBColor(1,1,1,0.65) weight:UIFontWeightSemibold]; [chartCard addSubview:chartTitle];
    self.chartView = [GPUChartView new]; self.chartView.translatesAutoresizingMaskIntoConstraints = NO; [chartCard addSubview:self.chartView];
    self.displayLabel = [self label:@"系统 display link 估计  —" size:13 color:GBColor(1,1,1,0.65) weight:UIFontWeightRegular]; [chartCard addSubview:self.displayLabel];
    self.bannerLabel = [self label:@"" size:12 color:GBColor(1,0.75,0.35,1) weight:UIFontWeightMedium]; self.bannerLabel.numberOfLines = 0; [chartCard addSubview:self.bannerLabel];

    NSDictionary *views = NSDictionaryOfVariableBindings(subtitle,targetTitle, _fpsControl, _loadLabel, _loadSlider, _runButton, _exportButton, metrics, chartCard, gpuCaption, _gpuFPSLabel, _avgLabel, _p95Label, _rendererLabel, _elapsedLabel, _missedLabel, chartTitle, _chartView, _displayLabel, _bannerLabel);
    subtitle.numberOfLines = 0;
    self.exportButton.enabled = NO;
    [content addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"V:|[subtitle]-18-[targetTitle]-8-[_fpsControl(34)]-16-[_loadLabel]-4-[_loadSlider(30)]-18-[_runButton(46)]-18-[metrics]-16-[chartCard]|" options:0 metrics:nil views:views]];
    [content addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|[subtitle]|" options:0 metrics:nil views:views]];
    [content addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|[targetTitle]|" options:0 metrics:nil views:views]];
    [content addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|[_fpsControl]|" options:0 metrics:nil views:views]];
    [content addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|[_loadLabel]|" options:0 metrics:nil views:views]];
    [content addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|[_loadSlider]|" options:0 metrics:nil views:views]];
    [content addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|[_runButton]-12-[_exportButton(100)]|" options:0 metrics:nil views:views]];
    [NSLayoutConstraint activateConstraints:@[
        [self.exportButton.centerYAnchor constraintEqualToAnchor:self.runButton.centerYAnchor],
        [self.exportButton.heightAnchor constraintEqualToAnchor:self.runButton.heightAnchor]
    ]];
    [content addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|[metrics]|" options:0 metrics:nil views:views]];
    [content addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|[chartCard]|" options:0 metrics:nil views:views]];
    [metrics addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[gpuCaption]-16-|" options:0 metrics:nil views:NSDictionaryOfVariableBindings(gpuCaption)]];
    [metrics addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[_gpuFPSLabel]" options:0 metrics:nil views:views]];
    [metrics addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[_avgLabel]-16-|" options:0 metrics:nil views:views]];
    [metrics addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[_p95Label]-16-|" options:0 metrics:nil views:views]];
    [metrics addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[_rendererLabel]-16-|" options:0 metrics:nil views:views]];
    [metrics addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[_elapsedLabel]-16-|" options:0 metrics:nil views:views]];
    [metrics addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[_missedLabel]-16-|" options:0 metrics:nil views:views]];
    [metrics addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"V:|-14-[gpuCaption]-1-[_gpuFPSLabel]-3-[_avgLabel]-6-[_p95Label]-8-[_rendererLabel]-8-[_elapsedLabel]-8-[_missedLabel]-16-|" options:0 metrics:nil views:views]];
    [chartCard addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[chartTitle]-16-|" options:0 metrics:nil views:views]];
    [chartCard addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-10-[_chartView]-10-|" options:0 metrics:nil views:views]];
    [chartCard addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[_displayLabel]-16-|" options:0 metrics:nil views:views]];
    [chartCard addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"H:|-16-[_bannerLabel]-16-|" options:0 metrics:nil views:views]];
    [chartCard addConstraints:[NSLayoutConstraint constraintsWithVisualFormat:@"V:|-14-[chartTitle]-2-[_chartView(180)]-2-[_displayLabel]-6-[_bannerLabel]-14-|" options:0 metrics:nil views:views]];
}

- (void)loadChanged:(UISlider *)slider {
    self.loadLabel.text = [NSString stringWithFormat:@"负载等级 %.0f / 8", slider.value];
}

- (void)setControlsEnabled:(BOOL)enabled {
    self.fpsControl.enabled = enabled;
    self.loadSlider.enabled = enabled;
    self.exportButton.enabled = enabled && self.latestStats != nil;
}

- (void)toggleRun {
    if (self.testing) { [self stopBenchmark]; return; }
    [self startBenchmark];
}

- (void)startBenchmark {
    if (self.testing) return;
    self.testing = YES;
    NSUInteger generation = ++self.sessionGeneration;
    NSUInteger fps = self.fpsControl.selectedSegmentIndex == 1 ? 120 : 60;
    NSUInteger load = (NSUInteger)llround(self.loadSlider.value);
    [self.chartValues removeAllObjects]; [self.samples removeAllObjects]; self.latestStats = nil;
    self.chartView.values = self.chartValues; self.chartView.maximum = (CGFloat)fps;
    self.gpuFPSLabel.text = @"—"; self.avgLabel.text = @"平均完成耗时  —";
    self.p95Label.text = @"P95 完成耗时  —"; self.rendererLabel.text = @"渲染器  初始化中…";
    self.elapsedLabel.text = @"已完成  0 帧 · 0.0 s"; self.missedLabel.text = @"错过目标节拍  —";
    self.bannerLabel.text = @"每次测试 20 秒，可随时停止。";
    self.runButton.backgroundColor = GBColor(0.74,0.25,0.28,1);
    [self.runButton setTitle:@"停止测试" forState:UIControlStateNormal];
    [self setControlsEnabled:NO];
    self.displaySamples = 0; self.displayIntervalSum = 0; self.displayLastTimestamp = 0; self.displayEstimateHz = 0;
    self.displayLabel.text = @"系统 display link 估计  —";
    [self.displayLink invalidate];
    self.displayLink = [CADisplayLink displayLinkWithTarget:self selector:@selector(displayTick:)];
    if (@available(iOS 15.0, *)) self.displayLink.preferredFrameRateRange = CAFrameRateRangeMake(60, 120, fps);
    [self.displayLink addToRunLoop:NSRunLoop.mainRunLoop forMode:NSRunLoopCommonModes];
    __weak typeof(self) weakSelf = self;
    [self.engine startWithFPS:fps load:load update:^(NSDictionary *stats) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (weakSelf.sessionGeneration == generation) [weakSelf applyStats:stats];
        });
    } failure:^(NSString *message) {
        dispatch_async(dispatch_get_main_queue(), ^{
            if (weakSelf.sessionGeneration == generation) [weakSelf showFailure:message];
        });
    }];
    self.stopTimer = [NSTimer timerWithTimeInterval:20.0 target:self selector:@selector(autoStop)
        userInfo:nil repeats:NO];
    [[NSRunLoop mainRunLoop] addTimer:self.stopTimer forMode:NSRunLoopCommonModes];
}

- (void)stopBenchmark {
    self.testing = NO;
    [self.stopTimer invalidate]; self.stopTimer = nil;
    [self.engine stop];
    [self.displayLink invalidate]; self.displayLink = nil;
    self.runButton.backgroundColor = GBColor(0.12,0.56,0.82,1);
    [self.runButton setTitle:@"开始测试" forState:UIControlStateNormal];
    [self setControlsEnabled:YES];
}

- (void)autoStop { if (self.testing) [self stopBenchmark]; }

- (void)displayTick:(CADisplayLink *)link {
    /* targetTimestamp - timestamp is the system's display-link interval
     * estimate. It describes the app's observed scheduling cadence only; it
     * does not switch the panel mode or prove a physical refresh rate. */
    CFTimeInterval interval = link.targetTimestamp - link.timestamp;
    if (interval <= 0 && self.displayLastTimestamp > 0) {
        interval = link.timestamp - self.displayLastTimestamp;
    }
    if (interval > 0) { self.displaySamples++; self.displayIntervalSum += interval; }
    self.displayLastTimestamp = link.timestamp;
    if (self.displaySamples >= 5) {
        double hz = self.displayIntervalSum > 0 ? (double)self.displaySamples / self.displayIntervalSum : 0;
        self.displayEstimateHz = hz;
        self.displayLabel.text = [NSString stringWithFormat:@"系统 display link 估计  %.1f Hz", hz];
        if (self.fpsControl.selectedSegmentIndex == 1 && hz < 100) {
            self.bannerLabel.text = [NSString stringWithFormat:@"GPU 目标为 120 FPS；系统 display link 约 %.0f Hz，不能据此认定屏幕已处于 120 Hz 模式。", hz];
        } else {
            self.bannerLabel.text = @"每次测试 20 秒，可随时停止。";
        }
    }
}

- (void)applyStats:(NSDictionary *)stats {
    if (!stats) return;
    self.latestStats = [stats copy]; [self.samples addObject:stats];
    double fps = GBNumber(stats,@"fps"), avg = GBNumber(stats,@"avgMs"), p95 = GBNumber(stats,@"p95Ms");
    self.gpuFPSLabel.text = [NSString stringWithFormat:@"%.1f", fps];
    self.avgLabel.text = [NSString stringWithFormat:@"平均完成耗时  %.2f ms", avg];
    self.p95Label.text = [NSString stringWithFormat:@"P95 完成耗时  %.2f ms", p95];
    self.rendererLabel.text = [NSString stringWithFormat:@"渲染器  %@", GBString(stats,@"renderer")];
    self.elapsedLabel.text = [NSString stringWithFormat:@"已完成  %.0f 帧 · %.1f s", GBNumber(stats,@"frames"), GBNumber(stats,@"elapsed")];
    self.missedLabel.text = [NSString stringWithFormat:@"错过目标节拍  %.0f", GBNumber(stats,@"missed")];
    [self.chartValues addObject:@(fps)];
    if (self.chartValues.count > 60) [self.chartValues removeObjectAtIndex:0];
    self.chartView.values = self.chartValues;
    self.exportButton.enabled = !self.testing;
}

- (void)showFailure:(NSString *)message {
    [self stopBenchmark];
    UIAlertController *alert = [UIAlertController alertControllerWithTitle:@"GPU 基准失败" message:message ?: @"未知错误"
        preferredStyle:UIAlertControllerStyleAlert];
    [alert addAction:[UIAlertAction actionWithTitle:@"确定" style:UIAlertActionStyleDefault handler:nil]];
    [self presentViewController:alert animated:YES completion:nil];
}

- (void)exportJSON {
    NSMutableDictionary *result = [NSMutableDictionary dictionaryWithDictionary:self.latestStats ?: @{}];
    result[@"displayLinkEstimateHz"] = @(self.displayEstimateHz);
    result[@"samples"] = self.samples ?: @[];
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:result options:NSJSONWritingPrettyPrinted error:&error];
    if (!data) return;
    NSString *name = [NSString stringWithFormat:@"gpu-bench-%@.json", [NSDate date]];
    NSURL *url = [NSURL fileURLWithPath:[NSTemporaryDirectory() stringByAppendingPathComponent:name]];
    if (![data writeToURL:url options:NSDataWritingAtomic error:&error]) return;
    UIActivityViewController *share = [[UIActivityViewController alloc] initWithActivityItems:@[url] applicationActivities:nil];
    if (share.popoverPresentationController) share.popoverPresentationController.sourceView = self.exportButton;
    [self presentViewController:share animated:YES completion:nil];
}

- (void)appDidEnterBackground {
    if (self.testing) { self.pausedForBackground = YES; [self stopBenchmark]; }
}

- (void)appWillEnterForeground {
    if (self.pausedForBackground) {
        self.pausedForBackground = NO;
        self.bannerLabel.text = @"测试已因进入后台停止；请手动重新开始。";
    }
}

@end

@interface GPUBenchAppDelegate : UIResponder <UIApplicationDelegate>
@property(nonatomic, strong) UIWindow *window;
@end

@implementation GPUBenchAppDelegate
- (BOOL)application:(UIApplication *)application didFinishLaunchingWithOptions:(NSDictionary *)launchOptions {
    self.window = [[UIWindow alloc] initWithFrame:UIScreen.mainScreen.bounds];
    self.window.rootViewController = [[UINavigationController alloc] initWithRootViewController:[GPUBenchViewController new]];
    self.window.tintColor = GBColor(0.22,0.79,1,1);
    [self.window makeKeyAndVisible];
    return YES;
}
@end

int main(int argc, char *argv[]) {
    @autoreleasepool { return UIApplicationMain(argc, argv, nil, NSStringFromClass([GPUBenchAppDelegate class])); }
}
