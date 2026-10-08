#pragma once
#include <array>
struct Adafruit_PWMServoDriver{
  std::array<int,16> pulses{};
  explicit Adafruit_PWMServoDriver(int){}
  void begin(){}
  void setPWMFreq(int){}
  void setPWM(int channel,int,int ticks){pulses[channel]=ticks;}
};
