#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/string.hpp"

using namespace std::chrono_literals;

class Talker : public rclcpp::Node {
public:
    Talker() : Node("talker"), count_(0) {
        publisher_ = this->create_publisher<std_msgs::msg::String>("chat", 10);
        timer_ = this->create_wall_timer(
            1s, std::bind(&Talker::timer_callback, this));
    }

private:
    void timer_callback() {
        auto msg = std_msgs::msg::String();
        msg.data = "Hola ROS2 C++ " + std::to_string(count_++);
        RCLCPP_INFO(this->get_logger(), "Publicando: '%s'", msg.data.c_str());
        publisher_->publish(msg);
    }

    rclcpp::TimerBase::SharedPtr timer_;
    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr publisher_;
    size_t count_;
};

int main(int argc, char * argv[]) {
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<Talker>());
    rclcpp::shutdown();
    return 0;
}
