# Notes on the DiamondLightSource `pmac` EPICS driver (as used by PMAC Doctor)

Collected from the `dls-master` branch (pmacApp/Db templates, pmacController.cpp,
pmacHardwarePower.cpp, pmacHardwareTurbo.cpp) while building this tool.

## Controller PVs (`pmacController.template`, prefix `$(P)`)
* `$(P):PMACTYPE` (mbbi, `PMAC_VIM_cid`) - cid: 602413 PMAC, 603382 Geobrick, 602404 Clipper, 604020 Power PMAC
* `$(P):CPUNUMCORES`, `$(P):CPULOAD_0..3`, `$(P):SERVO_FREQ` (kHz; Turbo from I10, Power from Sys.ServoPeriod)
* `$(P):READ_CTRLSTAT1..3` + `CTRLSTAT:status1..3` (mbbiDirect) - global status (`???` Turbo / `?` Power)
* `$(P):READ_PLCBITS00/01` - PLC *disabled* bits; `$(P):READ_PROGBITS`; `$(P):READ_GPIO_INP/OUT`
* `$(P):GLOBAL_PROBLEM_RBV`, `$(P):FEEDRATE_RBV`, `$(P):FEEDRATE_LIMIT`, `$(P):FEEDRATE_PROBLEM_RBV`
* `$(P):I_VARIABLES_RBV`, `P_VARIABLES_RBV`, `M_VARIABLES_RBV` - waveform listing of the slow-polled variables
* `$(P):STAT_*` comms statistics, `$(P):FAST/MEDIUM/SLOW_SIZE_RBV`, `$(P):REPORT_*`
* `$(P):SendCmd` (waveform, asynOctetWrite `PMAC_C_WRITE_CMD`) - **write-only**, reply discarded
* `$(P):PollAllNow`, `StopAll` (`&*abort` / ^A), `KillAll` (`#*k` / ^K), `DeferMoves`, `AxesReadonly`
* dynamic parameters: `PMAC_V<T><S>_<var>` read (T = D double, I int, H hex, S string; S = F fast, M medium, S slow),
  `PMAC_W<T>_<var>` write - only usable from records compiled into the IOC

## Axis PVs (`pmacStatusAxis.template`, `$(PMAC):AXIS$(AXIS):`)
`POSITION` (`#nP`), `FOLL_ERROR` (`#nF`), `VELOCITY` (`#nV`), `READ_STAT1..3` (`PMAC_C_AXIS_BITS01..03`), `DESC`

### Status word mapping
* Power PMAC `#n?` -> `$` + 16 hex digits. Word1 bits: 31 TriggerMove, 30 HomeInProgress, 29 MinusLimit,
  28 PlusLimit, 27 FeWarn, 26 FeFatal, 25 LimitStop, 24 AmpFault, 23 SoftMinusLimit, 22 SoftPlusLimit,
  21 I2tFault, 20 TriggerNotFound, 19 AmpWarn, 18 EncLoss, 17 AuxFault, 15 HomeComplete, 14 DesVelZero,
  13 ClosedLoop, 12 AmpEna, 11 InPos, 10 BlockRequest, 9 PhaseFound (Diamond's parser uses 9/8).
  READ_STAT1 = bits 31-16 of word 1, READ_STAT2 = bits 15-0 of word 1, READ_STAT3 = bits 31-16 of word 2.
* Turbo `#n?` -> 12 hex digits, two 24-bit words. Word1: 23 Activated, 22 NegLimit, 21 PosLimit, 19 AmpEnabled,
  18 OpenLoop, 13 DesVelZero, 10 HomeSearch ... Word2: 0 InPos, 1 WarnFE, 2 FatalFE, 3 AmpFault, 5 I2T,
  6 IntegratedFatalFE, 8 PhaseRefErr, 9 PhaseSearchErr, 10 HomeComplete, 11 StoppedOnPosLimit,
  12 StoppedOnDesPosLimit, 15 AssignedToCS, 20-23 CS number - 1.
  READ_STAT1..3 = hex digits 1-4, 5-8, 9-12.
* Global: Power `?` = Sys.Status (bits 0-1 WDTFault, 2 PwrOnFault, 3 ProjectLoadErr, 4 ConfigLoadErr,
  5 HWChangeErr, 6 FileConfigErr, 7 Default, 8 NoClocks, 9 AbortAll, 10 BufSizeErr, 11 FlashSizeErr,
  12-14 CK3WConfigErr, 15 CK3WHWChange). Turbo `???` hardware-problem mask = bits 9,10,11,13,15,20.

## Motor records (`dls_pmac_asyn_motor.template`)
Motor record `$(P)$(M)` aliased to `$(PMAC):M$(ADDR)`; extra records `:FERROR`, `:FERRORMAX`, `:ELOSS`
(`P464+n`), `:HOMED`, `:SCALE`, `:KILL`, `DirectDemand`, `Mres`, `Offset`, `DirectMotorNumber`.
Overtravel limits are disabled during homing with `Ixx24 |= $20000`; the driver flags PROBLEM if that
bit is set while not homing.

## Communication
* Low level: output EOS LF, input EOS ACK (0x06), 2 s timeout, 1024-byte buffer; Power PMAC replies have LF
  between values (replaced by spaces by the broker); errors start with BEL (0x07).
* Power PMAC link = SSH + `gpascii -2`, `echo 7` for bare values. Turbo link = Delta Tau Ethernet protocol
  (port 1025, `VR_PMAC_GETRESPONSE` 0xBF with `VR_DOWNLOAD` 0x40, header = type, request, wValue, wIndex,
  wLength big-endian).
