import Foundation
import UIKit
import GlassBackgroundComponent

/// When a spring rests: `.glass` carries the distance-to-target threshold.
public enum NavigationSpringRest: Equatable {
    case glass(CGFloat)

    var threshold: CGFloat {
        switch self {
        case .glass(let threshold):
            return threshold
        }
    }
}

/// A navigation spring in the two parametrizations the glass settings use.
///
/// `dampingRatio`/`response` is the physical form (ζ, the response period in
/// seconds; stiffness = (2π / response)² at unit mass). `duration`/`bounce` is
/// the SwiftUI-style form: ζ = 1 - bounce, the response estimated as 55% of the
/// perceptual duration.
public struct NavigationTransitionSpring: Equatable {
    public let dampingRatio: CGFloat
    public let response: CGFloat

    public init(dampingRatio: Double, response: Double) {
        self.dampingRatio = CGFloat(dampingRatio)
        self.response = CGFloat(max(0.05, response))
    }

    public init(duration: Double, bounce: Double) {
        self.init(
            dampingRatio: max(0.02, 1.0 - bounce),
            response: max(0.05, duration * 0.55)
        )
    }

    var angularFrequency: CGFloat {
        return 2.0 * .pi / self.response
    }

    /// Advances a damped spring by `time` seconds (semi-implicit Euler, substepped
    /// for stability at display-link rates), returning the value and velocity.
    public func advance(value: CGFloat, velocity: CGFloat, target: CGFloat, time: CGFloat) -> (value: CGFloat, velocity: CGFloat) {
        let omega0 = self.angularFrequency
        let zeta = self.dampingRatio
        var position = value - target
        var velocity = velocity
        let steps = max(1, Int((time / 0.008).rounded(.up)))
        let dt = time / CGFloat(steps)
        for _ in 0 ..< steps {
            let acceleration = -omega0 * omega0 * position - 2.0 * zeta * omega0 * velocity
            velocity += acceleration * dt
            position += velocity * dt
        }
        return (value: target + position, velocity: velocity)
    }
}

/// A sampled damped-spring trajectory between two values on an absolute clock.
///
/// The clock's zero is the bake's origin: `value(at:)` evaluates the closed-form
/// spring from `startTime`, `rebased(by:)` shifts the origin, `retargeted(at:to:spring:)`
/// redirects from the current value and velocity.
public final class NavigationSpringCurve: Equatable {
    public static func == (lhs: NavigationSpringCurve, rhs: NavigationSpringCurve) -> Bool {
        return lhs === rhs
    }
    public let startTime: Double
    public let from: CGFloat
    public let target: CGFloat
    public let spring: NavigationTransitionSpring
    public let rest: NavigationSpringRest
    /// The absolute time the curve rests at (its band-entry, verified over three samples).
    public let restTime: Double
    /// The initial offset and velocity, in clock coordinates.
    private let initialOffset: CGFloat
    private let initialVelocity: CGFloat

    public init(startTime: Double, from: CGFloat, target: CGFloat, spring: NavigationTransitionSpring, rest: NavigationSpringRest) {
        self.startTime = startTime
        self.from = from
        self.target = target
        self.spring = spring
        self.rest = rest
        self.initialOffset = from - target
        self.initialVelocity = 0.0
        self.restTime = NavigationSpringCurve.solveRestTime(
            startTime: startTime, offset: from - target, velocity: 0.0,
            spring: spring, threshold: rest.threshold)
    }

    private init(startTime: Double, offset: CGFloat, velocity: CGFloat, target: CGFloat,
                 spring: NavigationTransitionSpring, rest: NavigationSpringRest, restTime: Double) {
        self.startTime = startTime
        self.from = target + offset
        self.target = target
        self.spring = spring
        self.rest = rest
        self.initialOffset = offset
        self.initialVelocity = velocity
        self.restTime = restTime
    }

    /// A curve that never moves.
    public static func constant(_ value: CGFloat, rest: NavigationSpringRest) -> NavigationSpringCurve {
        return NavigationSpringCurve(
            startTime: 0.0, offset: 0.0, velocity: 0.0, target: value,
            spring: NavigationTransitionSpring(dampingRatio: 1.0, response: 0.05),
            rest: rest, restTime: 0.0)
    }

    private func displacement(at time: Double) -> CGFloat {
        let dt = max(0.0, time - self.startTime)
        let omega0 = Double(self.spring.angularFrequency)
        let zeta = Double(self.spring.dampingRatio)
        let x0 = Double(self.initialOffset)
        let v0 = Double(self.initialVelocity)
        let t = dt
        if zeta < 1.0 {
            let omegaD = omega0 * (1.0 - zeta * zeta).squareRoot()
            let decay = Foundation.exp(-zeta * omega0 * t)
            let c1 = x0
            let c2 = (v0 + zeta * omega0 * x0) / omegaD
            return CGFloat(decay * (c1 * Foundation.cos(omegaD * t) + c2 * Foundation.sin(omegaD * t)))
        } else {
            let decay = Foundation.exp(-omega0 * t)
            return CGFloat(decay * (x0 + (v0 + omega0 * x0) * t))
        }
    }

    public func value(at time: Double) -> CGFloat {
        return self.target + self.displacement(at: time)
    }

    /// Central-difference velocity, clamped to the curve's start.
    public func velocity(at time: Double) -> CGFloat {
        let h = 0.004
        let after = self.value(at: time + h)
        let before = self.value(at: max(self.startTime, time - h))
        let span = (time + h) - max(self.startTime, time - h)
        guard span > 0.000001 else {
            return 0.0
        }
        return (after - before) / CGFloat(span)
    }

    /// Redirects toward `target` on `spring` from the value and velocity at `time`.
    public func retargeted(at time: Double, to target: CGFloat, spring: NavigationTransitionSpring) -> NavigationSpringCurve {
        let value = self.value(at: time)
        let velocity = self.velocity(at: time)
        return NavigationSpringCurve(
            startTime: time, offset: value - target, velocity: velocity, target: target,
            spring: spring, rest: self.rest,
            restTime: NavigationSpringCurve.solveRestTime(
                startTime: time, offset: value - target, velocity: velocity,
                spring: spring, threshold: self.rest.threshold))
    }

    /// Moves onto a clock whose zero is `time` into this curve's: the trajectory is unchanged,
    /// only re-indexed (a curve already in flight keeps its phase, a delayed one keeps its delay).
    public func rebased(by time: Double) -> NavigationSpringCurve {
        guard time != 0.0 else {
            return self
        }
        return NavigationSpringCurve(
            startTime: self.startTime - time, offset: self.initialOffset,
            velocity: self.initialVelocity, target: self.target,
            spring: self.spring, rest: self.rest, restTime: self.restTime - time)
    }

    /// Numeric band-entry: the first time the value stays within `threshold` of the
    /// target over three consecutive samples, capped at six seconds.
    private static func solveRestTime(startTime: Double, offset: CGFloat, velocity: CGFloat,
                                      spring: NavigationTransitionSpring, threshold: CGFloat) -> Double {
        guard offset != 0.0 || velocity != 0.0 else {
            return startTime
        }
        let probe = NavigationSpringCurve(
            startTime: 0.0, offset: offset, velocity: velocity, target: 0.0,
            spring: spring, rest: .glass(threshold), restTime: .infinity)
        let step = 1.0 / 120.0
        var inBand = 0
        var t = 0.0
        while t < 6.0 {
            if abs(probe.displacement(at: t)) <= threshold {
                inBand += 1
                if inBand >= 3 {
                    return startTime + t - 2.0 * step
                }
            } else {
                inBand = 0
            }
            t += step
        }
        return startTime + 6.0
    }
}

/// The sample window one bake installs as keyframes.
public struct NavigationKeyframeTiming {
    /// Absolute clock times of the samples, first to `duration` inclusive.
    public let sampleTimes: [Double]
    public let duration: Double
}

/// A display-link clock the platter keyframes run on: `start` opens (or extends) a
/// sampled window, `animationTime` reports the clock's time, `ended` fires at the end.
public final class NavigationAnimationClock: NSObject {
    private final class WeakProxy: NSObject {
        weak var clock: NavigationAnimationClock?

        @objc func tick() {
            self.clock?.tick()
        }
    }

    private weak var hostView: UIView?
    private var displayLink: CADisplayLink?
    private var windowStart: Double = 0.0
    private var windowDuration: Double = 0.0
    public var ended: (() -> Void)?

    public init(hostView: UIView) {
        self.hostView = hostView
        super.init()
    }

    public var isRunning: Bool {
        return self.displayLink != nil
    }

    /// The clock's current time: seconds into the open window, its duration once over.
    public func animationTime() -> Double {
        guard self.displayLink != nil else {
            return self.windowDuration
        }
        return min(max(0.0, CACurrentMediaTime() - self.windowStart), self.windowDuration)
    }

    /// Opens a sampling window. `continuing` keeps the display link alive across
    /// back-to-back bakes (the caller rebases its curves onto the new window's zero).
    public func start(duration: Double, continuing: Bool) -> NavigationKeyframeTiming {
        self.windowStart = CACurrentMediaTime()
        self.windowDuration = max(0.05, duration)
        let step = 1.0 / 60.0
        var times: [Double] = []
        var t = 0.0
        while t < self.windowDuration {
            times.append(t)
            t += step
        }
        times.append(self.windowDuration)
        if self.displayLink == nil {
            let proxy = WeakProxy()
            proxy.clock = self
            let link = CADisplayLink(target: proxy, selector: #selector(WeakProxy.tick))
            link.add(to: .main, forMode: .common)
            self.displayLink = link
        }
        return NavigationKeyframeTiming(sampleTimes: times, duration: self.windowDuration)
    }

    public func stop() {
        self.displayLink?.invalidate()
        self.displayLink = nil
    }

    private func tick() {
        guard self.displayLink != nil else {
            return
        }
        if CACurrentMediaTime() - self.windowStart >= self.windowDuration {
            self.stop()
            self.ended?()
        }
    }

    deinit {
        self.displayLink?.invalidate()
    }
}

/// Installs `values` sampled over `timing` as a keyframe animation on `key`.
///
/// The animation spans the sampled window (offset by its first sample) and holds its
/// final frame, which is the model the bake left in place.
public func addNavigationKeyframes(to layer: CALayer, key: String, keyPath: String,
                                   values: [Any], timing: NavigationKeyframeTiming) {
    guard let first = timing.sampleTimes.first,
          values.count == timing.sampleTimes.count else {
        return
    }
    let span = timing.duration - first
    guard span > 0.0001 else {
        return
    }
    let animation = CAKeyframeAnimation(keyPath: keyPath)
    animation.values = values
    animation.duration = span
    animation.beginTime = CACurrentMediaTime() + first
    animation.keyTimes = timing.sampleTimes.map { NSNumber(value: ($0 - first) / span) }
    animation.fillMode = .forwards
    animation.isRemovedOnCompletion = false
    layer.add(animation, forKey: key)
}

/// Keeps a glass container's inner geometry on the bake's final sample: the layer's
/// position and bounds are keyframed separately, the glass follows the model.
extension GlassBackgroundContainerView {
    public func animateSize(width: CGFloat, height: CGFloat, timing: NavigationKeyframeTiming, isDark: Bool) {
        self.update(
            size: CGSize(width: max(0.0, width), height: max(0.0, height)),
            isDark: isDark,
            transition: .immediate
        )
    }
}

extension GlassBackgroundView {
    public func animateShape(sizes: [CGSize], timing: NavigationKeyframeTiming, isDark: Bool,
                             tintColor: GlassBackgroundView.TintColor) {
        guard let size = sizes.last else {
            return
        }
        self.update(
            size: CGSize(width: max(0.0, size.width), height: max(0.0, size.height)),
            cornerRadius: max(0.0, size.height) * 0.5,
            isDark: isDark,
            tintColor: tintColor,
            transition: .immediate
        )
    }

    public func animateShape(width: CGFloat, height: CGFloat, timing: NavigationKeyframeTiming,
                             isDark: Bool, tintColor: GlassBackgroundView.TintColor) {
        self.animateShape(sizes: [CGSize(width: width, height: height)], timing: timing,
                          isDark: isDark, tintColor: tintColor)
    }
}
