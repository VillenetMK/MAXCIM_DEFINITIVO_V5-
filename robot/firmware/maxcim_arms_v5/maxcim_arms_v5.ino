// ESP32 + PCA9685, I2C SDA21/SCL22. Sin homing automático al arrancar.
// INIT/ENABLE solo después de calibrar y colocar manualmente el brazo en home.
#include <Wire.h>
#include <Adafruit_PWMServoDriver.h>
#include <stdio.h>
#include <string.h>

Adafruit_PWMServoDriver pca(0x40);
const uint8_t channels[6] = {0,1,2,4,5,6};
uint16_t current[6] = {0}, target[6] = {0};
uint8_t intervalMs[6] = {10,10,10,10,10,10};
uint32_t stepped[6] = {0}, lastPing = 0;
unsigned long groupId = 0;
bool initialized[6] = {false}, enabled = false;
char line[96];uint8_t used = 0;bool overflow = false;

int indexOf(int channel){for(int i=0;i<6;i++)if(channels[i]==channel)return i;return -1;}
void hold(){for(int i=0;i<6;i++)target[i]=current[i];}
void disable(){hold();enabled=false;for(int i=0;i<6;i++)pca.setPWM(channels[i],0,4096);}
void state(){Serial.print("STATE ");Serial.print(enabled?1:0);bool busy=false;for(int i=0;i<6;i++)busy|=target[i]!=current[i];Serial.print(busy?" 1 ":" 0 ");Serial.print(groupId);for(int i=0;i<6;i++){Serial.print(' ');Serial.print(current[i]);}Serial.println();}
void command(){
  int ch,ticks,ms,consumed=0;
  unsigned long group;
  if(strcmp(line,"HELLO")==0){Serial.println("MAXCIM_ARMS 5");return;}
  if(strcmp(line,"PING")==0){lastPing=millis();state();return;}
  if(strcmp(line,"STOP")==0){hold();state();return;}
  if(strcmp(line,"DISABLE")==0){disable();state();return;}
  if(sscanf(line,"G %lu %n",&group,&consumed)==1 && line[consumed]=='\0'){groupId=group;lastPing=millis();state();return;}
  if(strcmp(line,"ENABLE")==0){for(int i=0;i<6;i++)if(!initialized[i]){Serial.println("ERR INIT_REQUIRED");return;}enabled=true;lastPing=millis();for(int i=0;i<6;i++)pca.setPWM(channels[i],0,current[i]);state();return;}
  if(sscanf(line,"INIT %d %d %n",&ch,&ticks,&consumed)==2 && line[consumed]=='\0'){
    int i=indexOf(ch);if(enabled||i<0||ticks<80||ticks>600){Serial.println("ERR INIT");return;}current[i]=target[i]=ticks;initialized[i]=true;return;
  }
  consumed=0;
  if(sscanf(line,"M %d %d %d %n",&ch,&ticks,&ms,&consumed)==3 && line[consumed]=='\0'){
    int i=indexOf(ch);if(!enabled||i<0||ticks<80||ticks>600||ms<1||ms>100){Serial.println("ERR MOVE");return;}target[i]=ticks;intervalMs[i]=ms;stepped[i]=millis();return;
  }
  Serial.println("ERR COMMAND");
}
void setup(){Serial.begin(115200);Wire.begin(21,22);pca.begin();pca.setPWMFreq(50);disable();Serial.println("MAXCIM_ARMS 5");}
void loop(){
  uint32_t now=millis();
  if(enabled && uint32_t(now-lastPing)>350){hold();enabled=false;Serial.println("ERR WATCHDOG");/* Mantener último PWM: no soltar el peso del brazo. */}
  for(int i=0;i<6;i++)if(enabled&&current[i]!=target[i]&&uint32_t(now-stepped[i])>=intervalMs[i]){stepped[i]=now;current[i]+=(target[i]>current[i]?1:-1);pca.setPWM(channels[i],0,current[i]);}
  // Un límite por vuelta evita que una entrada continua impida el watchdog.
  for(int n=0;n<64&&Serial.available();n++){
    char c=Serial.read();if(c=='\r')continue;
    if(c=='\n'){if(!overflow){line[used]='\0';command();}else Serial.println("ERR OVERFLOW");used=0;overflow=false;}
    else if(used<sizeof(line)-1&&!overflow)line[used++]=c;
    else overflow=true;
  }
}
