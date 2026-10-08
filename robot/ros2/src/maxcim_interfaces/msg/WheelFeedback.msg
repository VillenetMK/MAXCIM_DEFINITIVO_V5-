# One physical MCU sample. Counts are absolute uint32 FALLING-edge FG pulses.
# header.stamp is the bridge's ROS receipt time, never a replay/restamp timer.
std_msgs/Header header
uint32 session
uint32 sequence
uint32 uptime_ms
uint32 left_ticks
uint32 right_ticks
float64 left_pps
float64 right_pps
uint16 left_pwm_permille
uint16 right_pwm_permille
uint16 status
uint32 command_ack
# Physical forward-only direction has been confirmed. A single FG cannot
# independently detect direction when externally pushed or back-driven.
bool direction_valid
