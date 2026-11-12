/* SPDX-License-Identifier: MIT
 * Small, hardware-gated GPU benchmark engine for the Sweet iOS app. */
#import <Foundation/Foundation.h>

NS_ASSUME_NONNULL_BEGIN

typedef void (^SweetGPUStatsBlock)(NSDictionary<NSString *, id> *stats);
typedef void (^SweetGPUFailureBlock)(NSString *message);

/// Renders an off-screen ES workload and reports completed GPU frames.
/// The engine accepts only 60 and 120 Hz targets. The reported frame time
/// includes command submission and glFinish; it is a completion-latency
/// measurement, not a panel-refresh measurement.
@interface SweetGPUEngine : NSObject
@property(nonatomic, readonly, getter=isRunning) BOOL running;
@property(nonatomic, copy, readonly) NSDictionary<NSString *, id> *snapshot;
- (void)startWithFPS:(NSUInteger)fps
                load:(NSUInteger)load
              update:(SweetGPUStatsBlock)update
             failure:(SweetGPUFailureBlock)failure;
- (void)stop;
@end

NS_ASSUME_NONNULL_END
