#include "ArduinoMock.h"
#include "Wire.h"
#include <cassert>
#include <iostream>
uint32_t fakeTime=0;SerialMock Serial;WireMock Wire;
#include "../../robot/firmware/maxcim_arms_v5/maxcim_arms_v5.ino"
void send(const std::string& text){Serial.feed(text+"\n");while(Serial.available())loop();}
int main(){
  setup();assert(!enabled);assert(pca.pulses[0]==4096);
  send("ENABLE");assert(!enabled);
  send("M 0 300 3");assert(target[0]==0);
  for(int channel:channels)send("INIT "+std::to_string(channel)+" 200");
  assert(pca.pulses[0]==4096);send("ENABLE");assert(enabled);assert(pca.pulses[0]==200);
  send("M 0 220 5");fakeTime=5;loop();assert(current[0]==201);
  send("G 3");assert(groupId==3);assert(Serial.output.find("STATE 1 1 3")!=std::string::npos);
  send("STOP");assert(target[0]==current[0]);fakeTime=100;loop();assert(current[0]==201);
  send("M 0 700 5");assert(target[0]==201);
  send("M 0 250 0");assert(target[0]==201);
  send("M 0 250 5 extra");assert(target[0]==201);
  send("M 0 250 5");fakeTime=106;loop();assert(current[0]==202);
  fakeTime=500;loop();assert(!enabled);assert(target[0]==current[0]);assert(pca.pulses[0]==202);
  send(std::string(150,'x'));assert(Serial.output.find("ERR OVERFLOW")!=std::string::npos);
  send("DISABLE");assert(pca.pulses[0]==4096);
  std::cout<<"PASS: ESP32 V5 parser, boot disabled, explicit initialization, group ack, hold STOP, limits, watchdog, bounded input\n";
}
