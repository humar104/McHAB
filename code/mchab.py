#!/usr/bin/python
from twisted.internet import task
from twisted.internet import reactor
import time
import datetime
import os
import serial
import math

import RPi.GPIO as GPIO
import Adafruit_MCP4725 as MCP4725

import L3G4200D as L3G
import LSM303DLM as LSM
import BMP085 as BMP
import attitude
import control

beeper_pin = 23
fuser_pin = 18
motor_pin = 4
mission_time = 120 * 60 * 1000.0 #120 mins. --> millisec

class PersistantVars:
    accel = []
    gyro = []
    mag = []
    mag_field = []

    NSEW_limits = [46*100+10, 45*100+25, 72*100+20, 73*100+20]

    estimated_euler = []
    ang_vel = []
    rpm = 0

    alt = 0
    pressure = 0
    temp = 0

    gps_fix = False
    boundary_reached = False
    fuser_count = 0
    fuser_fired = False

    beep_high = False
    beep_count = 0
    beep_gps = False
    beep_time = 0

    mission_start = False
    start_time = time.time()*1000.0
    mission_finished = False

    def __init__(self, lsm, l3g, bmp):
        self.lsm = lsm
        self.l3g = l3g
        self.bmp = bmp

def convert_gyro(array):
    conv = lambda x: (float(x)/(2**15))*250
    return [conv(array[0]),conv(array[1]),conv(array[2])]

def readBMP(arg):
    #print 'BMP read: ' + str(datetime.datetime.now()),
    arg[0].temp = arg[0].bmp.readTemperature()
    arg[0].pressure = arg[0].bmp.readPressure()
    arg[0].altitude = arg[0].bmp.readAltitude()

    print 'temp: ' + str(arg[0].temp) + ';pressure: ' + str(arg[0].pressure) + ';altitude: ' + str(arg[0].altitude)
    arg[1].write(str(datetime.datetime.now()) + '; temp: ' + str(arg[0].temp) + ';pressure: ' + str(arg[0].pressure) + ';altitude: ' + str(arg[0].altitude) + '\n')

def readGPS(arg):
    #If there's something in the serial buffer, get it
    while(arg[1].inWaiting()>0):
        line = arg[1].readline().rstrip().split(',')
        arg[1].write(str(datetime.datetime.now()) + str(line) + '\n')
        print line

        #Only look at the GPGGA sentence
        if(line[0]=='$GPGGA'):
            coord = []

            #Check if there's a fix
            if((line[6]=='1' or line[6]=='2') and arg[0].gps_fix==False):
                coord = [float(line[2]),float(line[4])]
                print 'We\'re locked at: ' + str(coord[0]) + ',' + str(coord[1])
                arg[2].write(str(datetime.datetime.now())+'; We\'re locked at: ' + str(coord[0]) + ',' + str(coord[1]) + '\n')
                arg[0].gps_fix=True

            #If there's a fix, check boundary conditions and altitude
            if(arg[0].gps_fix):
                coord = [float(line[2]),float(line[4])]

                if( coord[0] > arg[0].NSEW_limits[0] or coord[0] < arg[0].NSEW_limits[1] or coord[1] < arg[0].NSEW_limits[2] or coord[1] > arg[0].NSEW_limits[3] ):
                    print 'Reached the boundary limits'
                    arg[2].write(str(datetime.datetime.now())+'; Reached the boundary limits\n')
                    arg[0].boundary_reached = True

                if(float(line[9]) > 152.4 and not arg[0].mission_start):
                    arg[0].mission_start = True
                    print 'Reached 500ft, Mission Start'
                    arg[2].write(str(datetime.datetime.now()) + '; Reached 500ft, Mission Start\n')
                    arg[0].start_time = time.time()*1000.0

                #magField(arg, line)


def convertGPS(latitude, longitude):
    #take latitude and longitude strings with minutes and seconds
    #convert to degrees
    latitude = float(int(float(latitude)/100)) + (float(latitude)%100)/60
    longitude = float(int(float(longitude)/100)) + (float(longitude)%100)/60

    return latitude, longitude

def magField(arg, gps_str):
    #calculate magnetic field vector in inertial frame
    Re = 6378.1*(10**3)
    g0 = (-29496.5 + 11.4*3)*10**(-9)
    g1 = (-1585.9 + 16.7*3)*10**(-9)
    h1 = (4945.1 - 28.8*3)*10**(-9)

    lat_str = gps_str[2]
    long_str = gps_str[4]

    latitude, longitude = convertGPS(lat_str, long_str)

    if gps_str[5] == 'W':
        longitude = 360 - longitude
    if gps_str[3] == 'S':
        latitude = -latitude

    #coelevation
    theta = math.pi/2 - latitude*math.pi/180.0
    phi = longitude*math.pi/180.0

    rb = float(gps_str[9]) + Re

    br = 2*(Re/rb)**3*(g0*math.cos(theta)+(g1*math.cos(phi)+h1*math.sin(phi))*math.sin(theta))
    btheta = (Re/rb)**3*(g0*math.sin(theta)-(g1*math.cos(phi)+h1*math.sin(phi))*math.cos(theta))
    bphi = (Re/rb)**3*(g1*math.sin(phi)-h1*math.cos(phi))

    bx = br*math.sin(btheta)*math.cos(bphi)
    by = br*math.sin(btheta)*math.sin(bphi)
    bz = br*math.cos(btheta)

    arg[0].mag_field = [bx, by, bz]
    print arg[0].mag_field

def beeper(arg):
    if(not arg[0].gps_fix):
        if(not arg[0].beep_high):
            GPIO.output(beeper_pin,GPIO.HIGH)
            arg[0].beep_high = True
            print 'Beep HIGH'
        else:
            GPIO.output(beeper_pin,GPIO.LOW)
            print 'Beep LOW'
            arg[0].beep_time = arg[0].beep_time + 1
            if(arg[0].beep_time > 9):
                arg[0].beep_high = False
                arg[0].beep_time = 0

    elif(not arg[0].beep_gps and arg[0].gps_fix):
        if(not arg[0].beep_high):
            GPIO.output(beeper_pin,GPIO.HIGH)
            print 'Beep HIGH'
            arg[0].beep_high = True
        else:
            GPIO.output(beeper_pin,GPIO.LOW)
            print 'Beep LOW'
            arg[0].beep_count = arg[0].beep_count + 1
            arg[0].beep_high = False
            if(arg[0].beep_count > 4):
                arg[0].beep_gps = True

def fuser(arg):
    if(not arg[0].fuser_fired):
        if(arg[0].boundary_reached):
            GPIO.output(fuser_pin,GPIO.HIGH)
            print 'Fired fuser'
            arg[1].write('Fired fuser\n')
            arg[0].fuser_count = arg[0].fuser_count + 1

        elif(arg[0].mission_start and (time.time()*1000.0-arg[0].start_time > mission_time)):
            GPIO.output(fuser_pin,GPIO.HIGH)
            print 'Fired fuser'
            arg[1].write('Fired fuser\n')
            arg[0].fuser_count = arg[0].fuser_count + 1

        if(arg[0].fuser_count > 5):
            arg[0].fuser_fired = True
            GPIO.output(fuser_pin,GPIO.LOW)
            print 'Turned off fuser'
            arg[1].write('Turned off fuser\n')

def estimator(arg):
    arg[0].accel = arg[0].lsm.readRawAccel()
    arg[0].mag = arg[0].lsm.readRawMag()
    arg[0].gyro = arg[0].l3g.readRawGyro()
    arg[0].ang_vel = convert_gyro(arg[0].gyro)

    arg[0].estimated_euler = arg[1].getAttitude(arg[0])

    arg[2].write(str(datetime.datetime.now()) + 'accel: ' + str(arg[0].accel) + ';gyro: ' + str(arg[0].gyro) + ';mag: ' + str(arg[0].mag) + '\n')
    arg[3].write(str(datetime.datetime.now()) + str(arg[0].estimated_euler) + '\n')

def control_func(arg):
    kp = 0.05
    kd = 0.05
    tau = -kp*arg[0].estimated_euler[0]*math.pi/180.0 - kd*arg[0].ang_vel[2]*math.pi/180.0
    I_motor = 1.21*10**-4
    f_s = 10.0
    arg[0].rpm = arg[0].rpm + 1.0/f_s*tau/I_motor
    print arg[0].rpm

if __name__ == '__main__':
    #Create log files
    newpath = './log'
    if not os.path.exists(newpath):
        os.makedirs(newpath)
    imu_file = open(newpath+"/IMU.dat","w")
    att_file = open(newpath+"/att.dat","w")
    bmp_file = open(newpath+"/BMP.dat","w")
    gps_file = open(newpath+"/GPS.dat","w")
    console_file = open(newpath+"/console.dat","w")

    #Sensor Initializations
    l3g = L3G.L3G4200D()
    l3g.enableDefault()
    lsm = LSM.LSM303DLM()
    lsm.enableDefault()
    ser = serial.Serial('/dev/ttyAMA0', 4800, timeout=0.1)
    att = attitude.attitude()
    dac = MCP4725.MCP4725(0x60)
    con = control.control(dac)

    #GPIO Pin Initializations
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(beeper_pin,GPIO.OUT)
    GPIO.setup(fuser_pin,GPIO.OUT)
    GPIO.setup(motor_pin,GPIO.OUT)

    GPIO.output(beeper_pin,GPIO.LOW)
    GPIO.output(fuser_pin,GPIO.LOW)
    GPIO.output(motor_pin,GPIO.LOW)

    #Sampling Frequencies
    bmp_fs = 2.0
    gps_fs = 1.0
    estim_fs = 20.0
    con_fs = 10.0

    #Object container initializations
    persistent = PersistantVars(lsm, l3g, BMP.BMP085())

    #Task list
    bmp_task = task.LoopingCall(readBMP,[persistent, bmp_file, console_file]).start(1.0/bmp_fs)
    gps_task = task.LoopingCall(readGPS,[persistent, ser, gps_file, console_file]).start(1.0/gps_fs)
    beeper_task = task.LoopingCall(beeper,[persistent, console_file]).start(1.0)
    fuser_task = task.LoopingCall(fuser,[persistent, console_file]).start(1.0)
    estimater_task = task.LoopingCall(estimator,[persistent, att, imu_file, att_file, console_file]).start(1.0/estim_fs)
    #control_task = task.LoopingCall(control_func,[persistent, con, console_file]).start(1.0/con_fs)

    reactor.run()

