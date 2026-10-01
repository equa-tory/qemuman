# A small manager program for QEMU. Tested on Windows
# https://github.com/equa-tory/qemuman (forked from https://github.com/yeppiidev/qemu-manager)

import ctypes
import json
import os
import subprocess
import tkinter as tk
import atexit

from subprocess import PIPE
from subprocess import Popen, CalledProcessError, run, CREATE_NEW_CONSOLE

from tkinter.messagebox import showinfo as alert
from tkinter.messagebox import askyesno as confirm

from ttkthemes import ThemedTk
from tkinter import ACTIVE, E, LEFT, N, RIGHT, S, W
from tkinter import ttk, simpledialog
from shutil import which

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qemuman_config.json")
MAX_HISTORY = 15

qemu_process = None
qemu_type_box = None
cdrom_path = None

class NewImageDialog(object):
    root = None

    def __init__(self, on_create, default_dir=None):
        self.on_create = on_create
        self.default_dir = default_dir or os.getcwd()

        self.top = tk.Toplevel(NewImageDialog.root)
        self.top.title("HDD Image Wizard")
        self.top.geometry("450x230")
        self.top.wm_resizable(False, False)
        self.top.transient(NewImageDialog.root)

        ttk.Label(
            self.top, text="Create a new HDD Image", font=("Tahoma", 18)
        ).pack(anchor="w", padx=15, pady=(15, 8))

        ttk.Label(self.top, text="File name:").pack(anchor="w", padx=15)
        self.hdd_name_text = tk.StringVar(value="mydisk.qcow2")
        self.hdd_name = ttk.Entry(self.top, textvariable=self.hdd_name_text)
        self.hdd_name.pack(fill="x", padx=15, pady=(0, 6))
        self.hdd_name.focus()

        ttk.Label(self.top, text="Size (GB):").pack(anchor="w", padx=15)
        self.hdd_size = tk.StringVar(value="20")
        ttk.Spinbox(
            self.top, from_=1, to=2048, textvariable=self.hdd_size
        ).pack(fill="x", padx=15, pady=(0, 6))

        buttons = ttk.Frame(self.top)
        buttons.pack(fill="x", padx=15, pady=10)
        ttk.Button(buttons, text="Create", command=self.submit).pack(side=RIGHT)
        ttk.Button(buttons, text="Cancel", command=self.top.destroy).pack(
            side=RIGHT, padx=5
        )

    def submit(self):
        name = self.hdd_name_text.get().strip()
        if not name:
            alert("Invalid name", "Please enter a file name.", icon="error")
            return
        if not name.lower().endswith(".qcow2"):
            name += ".qcow2"

        try:
            size = int(self.hdd_size.get())
            if size < 1:
                raise ValueError
        except ValueError:
            alert("Invalid size", "Size must be a whole number of GB.", icon="error")
            return

        path = name if os.path.isabs(name) else os.path.join(self.default_dir, name)
        if os.path.exists(path) and not confirm(
            "File exists", f"{path} already exists. Overwrite it?", icon="warning"
        ):
            return

        if self.on_create(path, size):
            self.top.destroy()


class Manager:
    def __init__(self) -> None:
        self.root = ThemedTk()
        self.root.title("QEMU Manager")
        self.root.geometry("600x680")
        self.root.wm_resizable(False, False)

        self.root.style = ttk.Style()
        self.root.style.theme_use("arc")
        self.root.style.configure("raised.TButton", borderwidth=1)

        # Variables
        self.qemu_kill_on_exit = tk.BooleanVar()
        self.qemu_kill_on_exit.set(True)

        self.qemu_sdl_window = tk.BooleanVar()
        self.qemu_sdl_window.set(False)

        self.qemu_use_haxm = tk.BooleanVar()
        self.qemu_use_haxm.set(False)

        self.qemu_ram_gb = tk.StringVar(value="4")

        self.config = self.load_config()
        self.qemu_use_cdrom = tk.BooleanVar(value=self.config.get("use_cdrom", True))
        self.qemu_use_cdrom.trace_add("write", self.on_cdrom_toggle)

        self.qemu_use_phys = tk.BooleanVar(value=False)
        self.qemu_phys_path = tk.StringVar(value=r"\\.\PhysicalDrive")
        self.qemu_phys_readonly = tk.BooleanVar(value=True)

        self.qemu_ssh_enabled = tk.BooleanVar(value=True)
        self.qemu_ssh_host_port = tk.StringVar(value="2222")
        self.qemu_ssh_guest_port = tk.StringVar(value="22")

        atexit.register(self.exit_handler)

        self.create_menu_items()
        self.create_widgets()

        self.running = True
        self.root.config(menu=self.menubar)

        self.root.mainloop()

    def load_config(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        for key in ("iso_history", "hdd_history"):
            hist = data.get(key)
            data[key] = [p for p in hist if isinstance(p, str)] if isinstance(hist, list) else []
        return data

    def save_config(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.config, f, indent=2)
        except OSError:
            pass

    def remember_path(self, key, path):
        path = path.strip()
        if not path:
            return
        hist = [p for p in self.config[key] if p != path]
        self.config[key] = [path] + hist[: MAX_HISTORY - 1]
        self.save_config()
        box = self.cdrom_path if key == "iso_history" else self.hdd_path
        box["values"] = self.config[key]

    def on_cdrom_toggle(self, *_):
        self.config["use_cdrom"] = self.qemu_use_cdrom.get()
        self.save_config()
        if hasattr(self, "cdrom_path"):
            self.cdrom_path.configure(
                state="normal" if self.qemu_use_cdrom.get() else "disabled"
            )

    def list_physical_drives(self):
        # Windows only: returns ["\\.\PHYSICALDRIVE4 - Model (64 GB)", ...]
        script = (
            "Get-CimInstance Win32_DiskDrive | ForEach-Object { "
            "'{0} - {1} ({2} GB)' -f $_.DeviceID, $_.Model, [math]::Round($_.Size/1GB) }"
        )
        try:
            out = run(
                ["powershell", "-NoProfile", "-Command", script],
                stdout=PIPE, stderr=PIPE, text=True, timeout=20,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return []
        return [line.strip() for line in out.splitlines() if line.strip()]

    def refresh_drives(self):
        self.phys_box["values"] = self.list_physical_drives()

    def is_admin(self):
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except (AttributeError, OSError):
            return True

    def find_aarch64_firmware(self):
        # edk2 UEFI firmware ships in <qemu dir>\share
        exe = which("qemu-system-aarch64")
        if not exe:
            return None
        base = os.path.dirname(exe)
        for rel in ("share/edk2-aarch64-code.fd", "edk2-aarch64-code.fd"):
            path = os.path.join(base, rel)
            if os.path.exists(path):
                return path
        return None

    def is_tool(self, name):
        # Check whether `name` is on PATH and marked as executable
        # https://stackoverflow.com/a/34177358/15871490

        return which(name) is not None

    def exit_handler(self):
        if self.qemu_kill_on_exit.get() and hasattr(self, 'qemu_process'):
            self.qemu_process.kill()

    def kill_vm(self):
        # Show an error message if the VM is not running
        try:
            if not self.qemu_process.poll() is None:
                alert(
                    title="VM is not running", message="The VM is not running",
                )
                return 1
        except:
            alert(
                title="VM is not running", message="The VM is not running",
            )
            return 1

        # Do you really want to terminate the VM?
        if (
            confirm(
                "Terminate QEMU",
                "Are you sure you want to terminate the QEMU process? Any unsaved changes inside the virtual machine will be lost!",
                icon="warning",
            )
            == "yes"
        ):
            # TODO: Make this work lol
            self.qemu_process.kill()

    def create_hdd(self, path, size_gb):
        if not self.is_tool("qemu-img"):
            alert(
                "Unable to create the image",
                "qemu-img was not found on your PATH. Please install QEMU and try again.",
                icon="error",
            )
            return False

        try:
            result = run(
                ["qemu-img", "create", "-f", "qcow2", path, f"{size_gb}G"],
                stdout=PIPE,
                stderr=PIPE,
                text=True,
            )
        except OSError as e:
            alert("Unable to create the image", str(e), icon="error")
            return False

        if result.returncode != 0:
            alert("qemu-img returned an error", result.stderr or result.stdout, icon="error")
            return False

        self.hdd_path_text.set(path)
        self.remember_path("hdd_history", path)
        alert("HDD created", f"Created {size_gb} GB image:\n{path}")
        return True

    def create_image(self):
        NewImageDialog.root = self.root
        NewImageDialog(self.create_hdd)

    def start_vm(self):
        try:
            # Check if QEMU is installed
            if not self.is_tool(self.qemu_type_box.get()):
                alert(
                    "Unable to start the VM",
                    "It seems like QEMU is not installed on your system. Please install it and try again.",
                    icon="error",
                )
                return 1

            use_cdrom = self.qemu_use_cdrom.get()
            hdd = self.hdd_path.get().strip()

            use_phys = self.qemu_use_phys.get()
            phys = self.qemu_phys_path.get().split(" - ")[0].strip()
            arm = self.qemu_type_box.get() == "qemu-system-aarch64"

            # Without a CD-ROM there must be something to boot from
            if not use_cdrom and not use_phys and not os.path.exists(hdd):
                alert(
                    "Unable to start the VM",
                    "CD-ROM is disabled, so the HDD file or a physical drive must be set to boot from.",
                    icon="error",
                )
                return 1

            if use_phys:
                prefix = "\\\\.\\PHYSICALDRIVE"
                if not phys.upper().startswith(prefix) or not phys[len(prefix):].isdigit():
                    alert(
                        "Unable to start the VM",
                        "Physical drive must look like \\\\.\\PhysicalDrive4",
                        icon="error",
                    )
                    return 1
                if not self.is_admin() and not confirm(
                    "Administrator required",
                    "Raw access to a physical drive needs Administrator rights and this program isn't elevated, so QEMU will probably fail to open it.\n\nStart anyway?",
                    icon="warning",
                ):
                    return 1

            # Check if the CD-ROM file exists
            if use_cdrom and not os.path.exists(self.cdrom_path.get()):
                alert(
                    "Unable to start the VM",
                    "The specified CD-ROM file does not exist",
                    icon="error",
                )
                return 1

            # Validate RAM amount
            try:
                ram_gb = float(self.qemu_ram_gb.get())
                if ram_gb <= 0:
                    raise ValueError
            except ValueError:
                alert(
                    "Unable to start the VM",
                    "RAM must be a positive number of GB.",
                    icon="error",
                )
                return 1

            # Validate SSH port forwarding
            nic = []
            if self.qemu_ssh_enabled.get():
                try:
                    host_port = int(self.qemu_ssh_host_port.get())
                    guest_port = int(self.qemu_ssh_guest_port.get())
                    if not (1 <= host_port <= 65535 and 1 <= guest_port <= 65535):
                        raise ValueError
                except ValueError:
                    alert(
                        "Unable to start the VM",
                        "SSH ports must be whole numbers between 1 and 65535.",
                        icon="error",
                    )
                    return 1
                nic = ["-nic", f"user,hostfwd=tcp::{host_port}-:{guest_port}"]

            cmd = [
                self.qemu_type_box.get(),
                "-m", str(int(ram_gb * 1024)),
            ]
            if arm:
                # The virt machine has no IDE/VGA/USB by default
                cmd += ["-M", "virt", "-cpu", "cortex-a72"]
                firmware = self.find_aarch64_firmware()
                if firmware:
                    cmd += ["-bios", firmware]
                cmd += [
                    "-device", "ramfb",
                    "-device", "qemu-xhci",
                    "-device", "usb-kbd",
                    "-device", "usb-mouse",
                    "-serial", "stdio",
                ]

            if use_cdrom:
                if arm:
                    cmd += [
                        "-drive", f"file={self.cdrom_path.get()},media=cdrom,if=none,id=cd0,readonly=on",
                        "-device", "usb-storage,drive=cd0",
                    ]
                else:
                    cmd += ["-cdrom", self.cdrom_path.get()]
            if hdd and os.path.exists(hdd):
                if arm:
                    cmd += ["-drive", f"file={hdd},if=virtio"]
                else:
                    cmd += ["-hda", hdd]
                if not use_cdrom and not use_phys:
                    cmd += ["-boot", "c"]
            if use_phys:
                ro = ",readonly=on" if self.qemu_phys_readonly.get() else ""
                if arm:
                    cmd += ["-drive", f"file={phys},format=raw,if=virtio{ro}"]
                else:
                    cmd += [
                        "-drive", f"file={phys},format=raw,if=none,id=physdrv{ro}",
                        "-device", "qemu-xhci",
                        "-device", "usb-storage,drive=physdrv,bootindex=0",
                    ]
            cmd += nic
            if self.qemu_sdl_window.get():
                cmd += ["-sdl"]
            if self.qemu_use_haxm.get() and not arm:
                cmd += ["-accel", "hax"]

            if use_cdrom:
                self.remember_path("iso_history", self.cdrom_path.get())
            if hdd:
                self.remember_path("hdd_history", hdd)

            # Open QEMU in the background using subprocess.Popen()
            if arm:
                # -serial stdio: give QEMU its own console for the serial output
                self.qemu_process = Popen(cmd, creationflags=CREATE_NEW_CONSOLE)
            else:
                self.qemu_process = Popen(cmd, stdout=PIPE, stderr=PIPE)

        except CalledProcessError as e:
            # TODO: Improve error messages
            alert(
                title="QEMU Returned an error",
                message=f"QEMU Returned an error: {str(e.output)}",
            )

    def get_first_file_with_ext(self, path, ext):
        # Loop through the specified directory and
        # find a file that has the specified extension
        for root, dirs, files in os.walk(path):
            for file in files:
                if file.endswith(ext):
                    return file

        # Return an empty string if no file with the
        # specified extension was found
        return ""

    def not_implemented(self):
        alert("Not Implemented", "This feature has not been implemented yet :P")

    def create_menu_items(self):
        # Create the menu bar
        self.menubar = tk.Menu(self.root)

        # Adding File Menu and commands
        file = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="File", menu=file)
        file.add_command(label="Preferences...", command=self.not_implemented)
        file.add_separator()
        file.add_command(label="Exit", command=self.root.destroy)

        virtual_machine = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Machine", menu=virtual_machine)
        virtual_machine.add_command(label="Start", command=self.start_vm)
        virtual_machine.add_command(label="Terminate", command=self.kill_vm)

        tools = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Tools", menu=tools)
        tools.add_command(label="Create HDD Image", command=self.create_image)

        about = tk.Menu(self.menubar, tearoff=0)
        self.menubar.add_cascade(label="Help", menu=about)
        about.add_command(
            label="About",
            command=lambda: alert(
                "About",
                "QEMU Manager is a simple GUI frontend for QEMU written in TKinter and Python.\n\nCreated by yeppiidev",
            ),
        )

    def create_widgets(self):
        # Which formatter should I use to make this look better?
        title_label = ttk.Label(
            self.root,
            text="QEMU Manager",
            anchor="w",
            background=self.root.cget("background"),
            font=("Tahoma", 30),
        )
        title_label.pack(fill="both", padx=18, pady=18)

        # Add a button to start the VM
        self.start_vm_btn = ttk.Button(
            self.root, text="Start VM", command=self.start_vm
        )
        self.start_vm_btn.pack(ipadx=10, ipady=10, padx=10, pady=10)
        self.start_vm_btn.place(x=490, y=625)

        # Add a button to kill the VM
        self.kill_vm_btn = ttk.Button(
            self.root, text="Terminate QEMU", command=self.kill_vm
        )
        self.kill_vm_btn.pack(ipadx=10, ipady=10, padx=10, pady=10)
        self.kill_vm_btn.place(x=360, y=625)

        qemu_type_box_label = ttk.Label(
            self.root, text="CPU Architecture:", background=self.root.cget("background")
        )
        qemu_type_box_label.pack(fill="x", padx=15)

        self.qemu_type_box_value = tk.StringVar()
        self.qemu_type_box_value.set("qemu-system-i386")

        self.qemu_type_box = ttk.Combobox(
            self.root, state="readonly", textvariable=self.qemu_type_box_value
        )
        self.qemu_type_box["values"] = (
            "qemu-system-i386",
            "qemu-system-x86_64",
            "qemu-system-ppc",
            "qemu-system-ppc64",
            "qemu-system-aarch64",
        )
        self.qemu_type_box.current(1)
        self.qemu_type_box.pack(fill=tk.X, padx=15, pady=5)

        cdrom_toggle = ttk.Checkbutton(
            self.root,
            text="Use CD-ROM (ISO) File Path:",
            variable=self.qemu_use_cdrom,
            offvalue=False,
            onvalue=True,
        )
        cdrom_toggle.pack(fill="x", padx=15, pady=5)

        self.cdrom_path_text = tk.StringVar()
        iso_hist = self.config["iso_history"]
        self.cdrom_path_text.set(
            iso_hist[0] if iso_hist else self.get_first_file_with_ext(os.getcwd(), ".iso")
        )

        # Editable dropdown: type a path or pick one from the history
        self.cdrom_path = ttk.Combobox(
            self.root, textvariable=self.cdrom_path_text, values=iso_hist
        )
        self.cdrom_path.pack(fill="x", padx=15)
        self.on_cdrom_toggle()
        self.cdrom_path.focus()

        hdd_path_label = ttk.Label(
            self.root,
            text="HDD (QCOW2) File Path:",
            background=self.root.cget("background"),
        )
        hdd_path_label.pack(fill="x", padx=15, pady=5)

        self.hdd_path_text = tk.StringVar()
        hdd_hist = self.config["hdd_history"]
        self.hdd_path_text.set(
            hdd_hist[0] if hdd_hist else self.get_first_file_with_ext(os.getcwd(), ".qcow2")
        )

        self.hdd_path_frame = tk.Frame(
            self.root, bg=self.root.cget("background"), width=450, height=50
        )
        self.hdd_path_frame.grid_columnconfigure(0, weight=1)

        self.hdd_path = ttk.Combobox(
            self.hdd_path_frame, textvariable=self.hdd_path_text, values=hdd_hist
        )
        self.hdd_path.grid(padx=(15, 0), row=0, column=0, columnspan=1, sticky="we")
        self.hdd_path.focus()

        self.create_hdd_btn = ttk.Button(
            self.hdd_path_frame, text="Create HDD", command=self.create_image
        )
        self.create_hdd_btn.grid(row=0, column=1, padx=(5, 15))

        self.hdd_path_frame.pack(fill="x")

        phys_check = ttk.Checkbutton(
            self.root,
            text="Boot from physical drive / USB (needs Administrator):",
            variable=self.qemu_use_phys,
            offvalue=False,
            onvalue=True,
        )
        phys_check.pack(fill="x", padx=15, pady=(10, 2))

        phys_frame = tk.Frame(self.root, bg=self.root.cget("background"))
        phys_frame.grid_columnconfigure(0, weight=1)
        self.phys_box = ttk.Combobox(phys_frame, textvariable=self.qemu_phys_path)
        self.phys_box.grid(row=0, column=0, sticky="we", padx=(15, 0))
        ttk.Button(phys_frame, text="Detect", command=self.refresh_drives).grid(
            row=0, column=1, padx=(5, 15)
        )
        phys_frame.pack(fill="x")

        ttk.Checkbutton(
            self.root,
            text="Read-only (recommended, protects the drive)",
            variable=self.qemu_phys_readonly,
            offvalue=False,
            onvalue=True,
        ).pack(fill="x", padx=15, pady=2)

        ram_label = ttk.Label(
            self.root, text="RAM (GB):", background=self.root.cget("background")
        )
        ram_label.pack(fill="x", padx=15, pady=(10, 0))

        self.qemu_ram_box = ttk.Spinbox(
            self.root, from_=1, to=1024, textvariable=self.qemu_ram_gb
        )
        self.qemu_ram_box.pack(fill="x", padx=15, pady=5)

        ssh_check = ttk.Checkbutton(
            self.root,
            text="Forward SSH port (connect with: ssh -p <host port> user@localhost)",
            variable=self.qemu_ssh_enabled,
            offvalue=False,
            onvalue=True,
        )
        ssh_check.pack(fill="x", padx=15, pady=(10, 2))

        ssh_frame = tk.Frame(self.root, bg=self.root.cget("background"))
        ssh_frame.pack(fill="x", padx=15)
        ttk.Label(
            ssh_frame, text="Host port:", background=self.root.cget("background")
        ).pack(side=LEFT)
        ttk.Spinbox(
            ssh_frame, from_=1, to=65535, width=7, textvariable=self.qemu_ssh_host_port
        ).pack(side=LEFT, padx=(5, 15))
        ttk.Label(
            ssh_frame, text="Guest port:", background=self.root.cget("background")
        ).pack(side=LEFT)
        ttk.Spinbox(
            ssh_frame, from_=1, to=65535, width=7, textvariable=self.qemu_ssh_guest_port
        ).pack(side=LEFT, padx=5)

        qemu_sdl_window = ttk.Checkbutton(
            self.root,
            text="Use SDL as the window library?",
            variable=self.qemu_sdl_window,
            offvalue=False,
            onvalue=True,
        )
        qemu_sdl_window.pack(fill="x", padx=15, pady=(10, 2))

        qemu_use_haxm = ttk.Checkbutton(
            self.root,
            text="Use Intel HAXM? (Requires you to have HAXM installed)",
            variable=self.qemu_use_haxm,
            offvalue=False,
            onvalue=True,
        )
        qemu_use_haxm.pack(fill="x", padx=15, pady=2)

        qemu_kill_on_exit_box = ttk.Checkbutton(
            self.root,
            text="Terminate QEMU on Exit?",
            variable=self.qemu_kill_on_exit,
            offvalue=False,
            onvalue=True,
        )
        qemu_kill_on_exit_box.pack(fill="x", padx=15, pady=2)

        # Uncomment for fun :]
        # self.selected_theme = tk.StringVar()

        # for theme_name in self.root.get_themes():
        #     self.rb = ttk.Radiobutton(
        #         self.root,
        #         text=theme_name,
        #         value=theme_name,
        #         variable=self.selected_theme,
        #         command=(lambda: self.root.set_theme(theme_name=self.selected_theme.get()))
        #     )
        #     self.rb.pack(expand=True, fill='both')


# Start the manager by constructing a new class
manager = Manager()
