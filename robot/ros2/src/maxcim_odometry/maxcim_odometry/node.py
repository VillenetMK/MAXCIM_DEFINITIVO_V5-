"""ROS 2 wheel odometry from verified physical Nano feedback."""

import math

import rclpy
from geometry_msgs.msg import TransformStamped
from maxcim_interfaces.msg import WheelFeedback
from nav_msgs.msg import Odometry
from rclpy.node import Node
from tf2_ros import TransformBroadcaster

from .core import Calibration, Sample, WheelOdometry


def covariance_matrix(diagonal):
    """Keep uncertainty explicit, including axes that wheel odometry cannot observe."""
    if len(diagonal) != 6 or any(not math.isfinite(v) or v <= 0 for v in diagonal):
        raise ValueError("Covariance diagonals require six finite positive values")
    result = [0.0] * 36
    for index, value in enumerate(diagonal):
        result[index * 7] = float(value)
    return result


class WheelOdometryNode(Node):
    def __init__(self):
        super().__init__("wheel_odometry")
        if not self.has_parameter("use_sim_time"):
            self.declare_parameter("use_sim_time", False)
        if self.get_parameter("use_sim_time").value:
            raise ValueError("Physical wheel odometry requires use_sim_time=false")
        self.declare_parameter("calibration_confirmed", False)
        for name in (
            "left_wheel_radius_m", "right_wheel_radius_m", "wheel_separation_m",
            "left_pulses_per_revolution", "right_pulses_per_revolution",
        ):
            self.declare_parameter(name, 0.0)
        self.declare_parameter("max_wheel_speed_mps", 1.0)
        self.declare_parameter("max_sample_gap_s", 0.25)
        self.declare_parameter("max_feedback_age_s", 0.25)
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_link")
        self.declare_parameter("publish_tf", True)
        self.declare_parameter(
            "pose_covariance_diagonal", [0.02, 0.02, 1000000.0, 1000000.0, 1000000.0, 0.05]
        )
        self.declare_parameter(
            "twist_covariance_diagonal", [0.05, 1000000.0, 1000000.0, 1000000.0, 1000000.0, 0.1]
        )

        if not self.get_parameter("calibration_confirmed").value:
            raise ValueError(
                "Wheel odometry is disabled: measure both wheel radii, wheel separation "
                "and effective FG pulses per wheel revolution, then set calibration_confirmed=true."
            )
        parameter = lambda name: self.get_parameter(name).value
        self.core = WheelOdometry(Calibration(**{
            name: parameter(name) for name in Calibration.__dataclass_fields__
        }))
        self.odom_frame = parameter("odom_frame")
        self.base_frame = parameter("base_frame")
        for frame in (self.odom_frame, self.base_frame):
            if not frame or frame.startswith("/") or any(c.isspace() for c in frame):
                raise ValueError("Frames must be nonempty, without leading slash or whitespace")
        if self.odom_frame == self.base_frame:
            raise ValueError("odom_frame and base_frame must be different")
        self.pose_covariance = covariance_matrix(parameter("pose_covariance_diagonal"))
        self.twist_covariance = covariance_matrix(parameter("twist_covariance_diagonal"))
        self.publisher = self.create_publisher(Odometry, "/odom", 10)
        self.tf = TransformBroadcaster(self) if parameter("publish_tf") else None
        self.subscription = self.create_subscription(
            WheelFeedback, "/wheel_feedback", self.on_feedback, 10
        )
        self.get_logger().info(
            "Waiting for consecutive physical wheel samples; no feedback means no /odom or TF."
        )

    def on_feedback(self, message):
        if message.header.frame_id != self.base_frame:
            self.core.invalidate("wrong_feedback_frame")
            self.get_logger().warning(
                "Wheel feedback frame must equal configured base_frame: " + self.base_frame,
                throttle_duration_sec=2.0,
            )
            return
        sample = Sample(
            session=message.session,
            sequence=message.sequence,
            uptime_ms=message.uptime_ms,
            left_ticks=message.left_ticks,
            right_ticks=message.right_ticks,
            stamp_ns=message.header.stamp.sec * 1000000000 + message.header.stamp.nanosec,
            direction_valid=message.direction_valid,
            status=message.status,
        )
        estimate = self.core.accept(sample, self.get_clock().now().nanoseconds)
        if estimate is None:
            if self.core.last_reason not in ("session_baseline", "baseline"):
                self.get_logger().warning(
                    "Wheel sample not integrated: " + self.core.last_reason,
                    throttle_duration_sec=2.0,
                )
            return

        odometry = Odometry()
        odometry.header.stamp = message.header.stamp
        odometry.header.frame_id = self.odom_frame
        odometry.child_frame_id = self.base_frame
        odometry.pose.pose.position.x = estimate.x
        odometry.pose.pose.position.y = estimate.y
        odometry.pose.pose.orientation.z = math.sin(estimate.yaw / 2.0)
        odometry.pose.pose.orientation.w = math.cos(estimate.yaw / 2.0)
        odometry.pose.covariance = self.pose_covariance
        odometry.twist.twist.linear.x = estimate.linear_velocity
        odometry.twist.twist.angular.z = estimate.angular_velocity
        odometry.twist.covariance = self.twist_covariance
        self.publisher.publish(odometry)

        if self.tf is not None:
            transform = TransformStamped()
            transform.header = odometry.header
            transform.child_frame_id = self.base_frame
            transform.transform.translation.x = estimate.x
            transform.transform.translation.y = estimate.y
            transform.transform.rotation = odometry.pose.pose.orientation
            self.tf.sendTransform(transform)


def main(args=None):
    rclpy.init(args=args)
    node = None
    try:
        node = WheelOdometryNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
