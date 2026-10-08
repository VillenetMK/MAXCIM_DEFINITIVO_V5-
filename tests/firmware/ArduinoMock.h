#pragma once
#include <cstdint>
#include <string>
#include <deque>
#include <sstream>
extern uint32_t fakeTime;
inline uint32_t millis(){return fakeTime;}
struct SerialMock{
  std::deque<char> input;std::string output;
  void begin(int){}
  int available(){return int(input.size());}
  char read(){char c=input.front();input.pop_front();return c;}
  template<typename T>void print(T value){std::ostringstream stream;stream<<value;output+=stream.str();}
  template<typename T>void println(T value){print(value);output+='\n';}
  void println(){output+='\n';}
  void feed(const std::string& s){for(char c:s)input.push_back(c);}
};
extern SerialMock Serial;
