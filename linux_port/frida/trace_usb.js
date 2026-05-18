// Frida agent for the REVASRI thermal camera APK.
//
// Hooks the libusb-1.0 fork (libusb100_dr.so), the libuvc fork (libuvc_dr.so),
// and libUVCCamera_dr.so's high-level cmd orchestrator. Streams structured
// events to the Python driver via send().

const MAX_BULK_DUMP = 256;   // bytes; large frame buffers are clipped here
const MAX_CTRL_DUMP = 1024;

function buf2hex(ptr, len, cap) {
  if (len <= 0 || ptr.isNull()) return "";
  const n = Math.min(len, cap || MAX_BULK_DUMP);
  try {
    const bytes = ptr.readByteArray(n);
    if (!bytes) return "";
    const u8 = new Uint8Array(bytes);
    let s = "";
    for (let i = 0; i < u8.length; i++) {
      s += (u8[i] < 16 ? "0" : "") + u8[i].toString(16);
    }
    return s;
  } catch (e) {
    return "<read-fail:" + e.message + ">";
  }
}

function findExport(libName, fnName) {
  const m = Process.findModuleByName(libName);
  if (!m) return null;
  return m.findExportByName(fnName);
}

// --- libusb_bulk_transfer / libusb_interrupt_transfer (sync) ----------------

function makeSyncTransferHook(kindLabel) {
  return {
    onEnter(args) {
      this.endpoint   = args[1].toInt32() & 0xff;
      this.buffer     = args[2];
      this.length     = args[3].toInt32();
      this.xferredPtr = args[4];
      this.isIn       = (this.endpoint & 0x80) !== 0;
      this.t0         = Date.now();
      this.kindLabel  = kindLabel;
      if (!this.isIn) {
        send({
          kind: kindLabel + "_out",
          ep: this.endpoint,
          req_len: this.length,
          hex: buf2hex(this.buffer, this.length),
        });
      }
    },
    onLeave(retval) {
      const r = retval.toInt32();
      const actual = this.xferredPtr.isNull() ? -1 : this.xferredPtr.readS32();
      if (this.isIn) {
        send({
          kind: this.kindLabel + "_in",
          ep: this.endpoint,
          req_len: this.length,
          got_len: actual,
          ret: r,
          dt_ms: Date.now() - this.t0,
          hex: buf2hex(this.buffer, actual),
        });
      } else {
        send({
          kind: this.kindLabel + "_out_done",
          ep: this.endpoint,
          sent_len: actual,
          ret: r,
          dt_ms: Date.now() - this.t0,
        });
      }
    }
  };
}

// --- libusb_control_transfer ------------------------------------------------

function makeControlHook() {
  return {
    onEnter(args) {
      this.bmReq   = args[1].toInt32() & 0xff;
      this.bReq    = args[2].toInt32() & 0xff;
      this.wValue  = args[3].toInt32() & 0xffff;
      this.wIndex  = args[4].toInt32() & 0xffff;
      this.buffer  = args[5];
      this.wLen    = args[6].toInt32() & 0xffff;
      this.isIn    = (this.bmReq & 0x80) !== 0;
      this.t0      = Date.now();
      if (!this.isIn) {
        send({
          kind: "ctrl_out",
          bmReq: this.bmReq, bReq: this.bReq,
          wValue: this.wValue, wIndex: this.wIndex,
          wLen: this.wLen,
          hex: buf2hex(this.buffer, this.wLen, MAX_CTRL_DUMP),
        });
      }
    },
    onLeave(retval) {
      const r = retval.toInt32();
      if (this.isIn) {
        send({
          kind: "ctrl_in",
          bmReq: this.bmReq, bReq: this.bReq,
          wValue: this.wValue, wIndex: this.wIndex,
          wLen: this.wLen,
          got_len: r,
          dt_ms: Date.now() - this.t0,
          hex: buf2hex(this.buffer, Math.max(0, r), MAX_CTRL_DUMP),
        });
      } else {
        send({
          kind: "ctrl_out_done",
          bmReq: this.bmReq, bReq: this.bReq,
          ret: r,
          dt_ms: Date.now() - this.t0,
        });
      }
    }
  };
}

// --- libusb_submit_transfer (async) -----------------------------------------
// struct libusb_transfer layout on 64-bit:
//   0  dev_handle*
//   8  flags  (u8)
//   9  endpoint (u8)
//  10  type (u8)
//  12  timeout (u32)
//  16  status (s32)
//  20  length (s32)
//  24  actual_length (s32)
//  32  callback (fn*)
//  40  user_data*
//  48  buffer*
//  56  num_iso_packets (s32)

const hookedCallbacks = new Set();
let nextXferId = 1;
const xferIds = new WeakMap(); // not really used, just a hint

function makeCallbackHook(callbackAddr) {
  return {
    onEnter(args) {
      const xfer = args[0];
      this.xfer = xfer;
      try {
        this.endpoint = xfer.add(9).readU8();
        this.type     = xfer.add(10).readU8();
        this.length   = xfer.add(20).readS32();
        this.actual   = xfer.add(24).readS32();
        this.status   = xfer.add(16).readS32();
        this.buffer   = xfer.add(48).readPointer();
      } catch (e) {
        send({ kind: "warn", msg: "callback read failed: " + e.message });
        return;
      }
      send({
        kind: "async_cb",
        ep: this.endpoint,
        type: this.type,
        req_len: this.length,
        got_len: this.actual,
        status: this.status,
        // Cap async dump aggressively — frame buffers are big.
        hex: buf2hex(this.buffer, this.actual, MAX_BULK_DUMP),
      });
    }
  };
}

function makeSubmitHook() {
  return {
    onEnter(args) {
      const xfer = args[0];
      try {
        const endpoint = xfer.add(9).readU8();
        const type     = xfer.add(10).readU8();
        const length   = xfer.add(20).readS32();
        const buffer   = xfer.add(48).readPointer();
        const cb       = xfer.add(32).readPointer();
        const isIn     = (endpoint & 0x80) !== 0;

        send({
          kind: "async_submit",
          ep: endpoint,
          type: type,
          length: length,
          is_in: isIn,
          callback: cb.toString(),
          // For OUT we dump what we're about to send.
          hex: isIn ? "" : buf2hex(buffer, length, MAX_BULK_DUMP),
        });

        // Attach a one-shot hook to the callback so we see the completion.
        const cbKey = cb.toString();
        if (!cb.isNull() && !hookedCallbacks.has(cbKey)) {
          try {
            Interceptor.attach(cb, makeCallbackHook(cb));
            hookedCallbacks.add(cbKey);
            send({ kind: "info", msg: "hooked async callback @ " + cbKey });
          } catch (e) {
            send({ kind: "warn", msg: "could not hook callback " + cbKey + ": " + e.message });
          }
        }
      } catch (e) {
        send({ kind: "warn", msg: "submit_transfer read failed: " + e.message });
      }
    }
  };
}

// --- vendor / libuvc high-level hooks ---------------------------------------

const hookedLibs = new Set();

function hookHighLevel() {
  if (!hookedLibs.has("libUVCCamera_dr.so")) {
    const uvccam = Process.findModuleByName("libUVCCamera_dr.so");
    if (uvccam) {
      for (const exp of uvccam.enumerateExports()) {
        const lc = exp.name.toLowerCase();
        if (lc.indexOf("sendcmd") < 0 && lc.indexOf("executecmd") < 0) continue;
        try {
          Interceptor.attach(exp.address, {
            onEnter(args) {
              let bufHex = "";
              let cmdLen = null;
              try {
                const len = args[2].toInt32();
                if (len > 0 && len < 4096) {
                  bufHex = buf2hex(args[1], Math.min(len, MAX_CTRL_DUMP));
                  cmdLen = len;
                }
              } catch (e) {}
              send({
                kind: "highlevel",
                sym: exp.name,
                arg0: args[0].toString(),
                cmd_len: cmdLen,
                cmd_hex: bufHex,
              });
            },
            onLeave(retval) {
              send({ kind: "highlevel_done", sym: exp.name, ret: retval.toInt32() });
            }
          });
          send({ kind: "info", msg: "hooked libUVCCamera_dr.so!" + exp.name + " @ " + exp.address });
        } catch (e) {
          send({ kind: "warn", msg: "hook failed for " + exp.name + ": " + e.message });
        }
      }
      hookedLibs.add("libUVCCamera_dr.so");
    }
  }

  if (!hookedLibs.has("libuvc_dr.so")) {
    const uvc = Process.findModuleByName("libuvc_dr.so");
    if (uvc) {
      const watch = ["uvc_stream_start", "uvc_stream_start_iso", "uvc_start_streaming",
                     "uvc_query_stream_ctrl", "uvc_stream_open_ctrl",
                     "uvc_set_ctrl", "uvc_get_ctrl"];
      for (const name of watch) {
        const addr = uvc.findExportByName(name);
        if (!addr) continue;
        Interceptor.attach(addr, {
          onEnter() { send({ kind: "uvc_call", sym: name }); },
          onLeave(retval) { send({ kind: "uvc_done", sym: name, ret: retval.toInt32() }); }
        });
        send({ kind: "info", msg: "hooked libuvc_dr.so!" + name + " @ " + addr });
      }
      hookedLibs.add("libuvc_dr.so");
    }
  }

  return hookedLibs.has("libUVCCamera_dr.so") && hookedLibs.has("libuvc_dr.so");
}

// --- libusb hooks (sync + control + async submit) ---------------------------

function hookLibusb() {
  const lib = "libusb100_dr.so";
  let any = false;

  const m = Process.findModuleByName(lib);
  if (!m) return false;

  const targets = [
    ["libusb_bulk_transfer",       makeSyncTransferHook("bulk")],
    ["libusb_interrupt_transfer",  makeSyncTransferHook("intr")],
    ["libusb_control_transfer",    makeControlHook()],
    ["libusb_submit_transfer",     makeSubmitHook()],
  ];
  for (const [fn, hook] of targets) {
    const addr = m.findExportByName(fn);
    if (!addr) { send({ kind: "warn", msg: lib + "!" + fn + " not exported" }); continue; }
    Interceptor.attach(addr, hook);
    send({ kind: "info", msg: "hooked " + lib + "!" + fn + " @ " + addr });
    any = true;
  }
  return any;
}

// --- ioctl() on /dev/bus/usb/... --------------------------------------------
// The vendor lib bypasses libusb wrappers and submits URBs via raw USBDEVFS
// ioctls. We hook ioctl in libc, filter on request type == 'U' (0x55), and
// dump the request-specific struct payloads.
//
// USBDEVFS request numbers we care about:
//   0  = USBDEVFS_CONTROL
//   2  = USBDEVFS_BULK
//  10  = USBDEVFS_SUBMITURB
//  11  = USBDEVFS_DISCARDURB
//  12  = USBDEVFS_REAPURB
//  13  = USBDEVFS_REAPURBNDELAY

const usbFds = new Set();   // populated lazily from successful USBDEVFS ioctls

function readBulkStruct(ptr) {
  // struct usbdevfs_bulktransfer {
  //   unsigned int ep; unsigned int len; unsigned int timeout; void *data;
  // };
  return {
    ep:      ptr.add(0).readU32(),
    len:     ptr.add(4).readU32(),
    timeout: ptr.add(8).readU32(),
    data:    ptr.add(16).readPointer(),  // 8-aligned after 3*u32+pad
  };
}

function readCtrlStruct(ptr) {
  // struct usbdevfs_ctrltransfer {
  //   uint8_t  bRequestType; uint8_t  bRequest;
  //   uint16_t wValue;       uint16_t wIndex;  uint16_t wLength;
  //   uint32_t timeout; void *data;
  // };
  return {
    bRequestType: ptr.add(0).readU8(),
    bRequest:     ptr.add(1).readU8(),
    wValue:       ptr.add(2).readU16(),
    wIndex:       ptr.add(4).readU16(),
    wLength:      ptr.add(6).readU16(),
    timeout:      ptr.add(8).readU32(),
    data:         ptr.add(16).readPointer(),
  };
}

function readUrbStruct(ptr) {
  // struct usbdevfs_urb { ... } — see linux/usbdevice_fs.h
  return {
    type:          ptr.add(0).readU8(),
    endpoint:      ptr.add(1).readU8(),
    status:        ptr.add(4).readS32(),
    flags:         ptr.add(8).readU32(),
    buffer:        ptr.add(16).readPointer(),
    buffer_length: ptr.add(24).readS32(),
    actual_length: ptr.add(28).readS32(),
  };
}

function hookIoctl() {
  const ioctl = Module.findGlobalExportByName("ioctl");
  if (!ioctl) {
    send({ kind: "warn", msg: "ioctl not exported (?!)" });
    return false;
  }
  Interceptor.attach(ioctl, {
    onEnter(args) {
      this.fd      = args[0].toInt32();
      this.req     = args[1].toUInt32();
      this.argp    = args[2];
      const type   = (this.req >> 8) & 0xff;
      this.isUsb   = (type === 0x55);  // 'U'
      this.nr      = this.req & 0xff;
      if (!this.isUsb) return;
      this.t0      = Date.now();

      if (this.nr === 0) {            // USBDEVFS_CONTROL
        try {
          const s = readCtrlStruct(this.argp);
          this.ctrl = s;
          const isIn = (s.bRequestType & 0x80) !== 0;
          if (!isIn && s.wLength > 0) {
            send({
              kind: "usbfs_ctrl_out",
              fd: this.fd,
              bRequestType: s.bRequestType, bRequest: s.bRequest,
              wValue: s.wValue, wIndex: s.wIndex, wLength: s.wLength,
              hex: buf2hex(s.data, s.wLength, MAX_CTRL_DUMP),
            });
          }
        } catch (e) { send({ kind: "warn", msg: "ctrl read fail: " + e.message }); }
      } else if (this.nr === 2) {     // USBDEVFS_BULK
        try {
          const s = readBulkStruct(this.argp);
          this.bulk = s;
          const isIn = (s.ep & 0x80) !== 0;
          if (!isIn) {
            send({
              kind: "usbfs_bulk_out",
              fd: this.fd,
              ep: s.ep & 0xff,
              len: s.len,
              hex: buf2hex(s.data, s.len),
            });
          }
        } catch (e) { send({ kind: "warn", msg: "bulk read fail: " + e.message }); }
      } else if (this.nr === 10) {    // USBDEVFS_SUBMITURB
        try {
          const u = readUrbStruct(this.argp);
          this.urb = u;
          this.urbAddr = this.argp;
          const isIn = (u.endpoint & 0x80) !== 0;
          send({
            kind: "usbfs_submit",
            fd: this.fd,
            ep: u.endpoint & 0xff,
            type: u.type,
            length: u.buffer_length,
            is_in: isIn,
            urb: this.urbAddr.toString(),
            hex: isIn ? "" : buf2hex(u.buffer, u.buffer_length),
          });
        } catch (e) { send({ kind: "warn", msg: "urb read fail: " + e.message }); }
      } else if (this.nr === 12 || this.nr === 13) {  // REAPURB / REAPURBNDELAY
        // argp is `void **` — kernel writes the urb ptr there on completion.
        this.reapArgp = this.argp;
      }
    },
    onLeave(retval) {
      if (!this.isUsb) return;
      const r = retval.toInt32();
      const dt = Date.now() - this.t0;
      usbFds.add(this.fd);

      if (this.nr === 0 && this.ctrl) {
        const s = this.ctrl;
        const isIn = (s.bRequestType & 0x80) !== 0;
        if (isIn && r >= 0) {
          send({
            kind: "usbfs_ctrl_in",
            fd: this.fd,
            bRequestType: s.bRequestType, bRequest: s.bRequest,
            wValue: s.wValue, wIndex: s.wIndex, wLength: s.wLength,
            ret: r,
            dt_ms: dt,
            hex: buf2hex(s.data, Math.max(0, r), MAX_CTRL_DUMP),
          });
        } else {
          send({ kind: "usbfs_ctrl_done", fd: this.fd, ret: r, dt_ms: dt });
        }
      } else if (this.nr === 2 && this.bulk) {
        const s = this.bulk;
        const isIn = (s.ep & 0x80) !== 0;
        if (isIn) {
          send({
            kind: "usbfs_bulk_in",
            fd: this.fd,
            ep: s.ep & 0xff,
            req_len: s.len,
            got_len: r,
            dt_ms: dt,
            hex: buf2hex(s.data, Math.max(0, r)),
          });
        } else {
          send({ kind: "usbfs_bulk_out_done", fd: this.fd, ret: r, dt_ms: dt });
        }
      } else if ((this.nr === 12 || this.nr === 13) && this.reapArgp && r === 0) {
        try {
          const urbPtr = this.reapArgp.readPointer();
          if (!urbPtr.isNull()) {
            const u = readUrbStruct(urbPtr);
            const isIn = (u.endpoint & 0x80) !== 0;
            send({
              kind: "usbfs_reap",
              fd: this.fd,
              ep: u.endpoint & 0xff,
              actual_length: u.actual_length,
              status: u.status,
              urb: urbPtr.toString(),
              hex: isIn ? buf2hex(u.buffer, u.actual_length) : "",
            });
          }
        } catch (e) { send({ kind: "warn", msg: "reap read fail: " + e.message }); }
      }
    }
  });
  send({ kind: "info", msg: "hooked ioctl @ " + ioctl });
  return true;
}

// --- driver -----------------------------------------------------------------

let ioctlHooked  = hookIoctl();
let libusbHooked = hookLibusb();
let hlHooked     = hookHighLevel();

if (!libusbHooked || !hlHooked) {
  const dlopen = Module.findGlobalExportByName("android_dlopen_ext")
              || Module.findGlobalExportByName("dlopen");
  if (dlopen) {
    Interceptor.attach(dlopen, {
      onEnter(args) { this.path = args[0].readCString(); },
      onLeave(retval) {
        if (!this.path) return;
        if (!libusbHooked && this.path.indexOf("libusb100_dr.so") >= 0) libusbHooked = hookLibusb();
        if (!hlHooked   && (this.path.indexOf("libUVCCamera_dr.so") >= 0
                         || this.path.indexOf("libuvc_dr.so") >= 0)) hlHooked = hookHighLevel();
      }
    });
    send({ kind: "info", msg: "watching dlopen for late-loaded vendor libs" });
  }
}

send({ kind: "info", msg: "agent loaded" });
