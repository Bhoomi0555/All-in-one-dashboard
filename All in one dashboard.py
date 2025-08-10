#!/usr/bin/env python3
"""
Combined Streamlit Dashboard (Tasks + Linux Executor + Docker + File Manager)
============================================================================
Four distinct workspaces in one page:

   simple web scraper.
2. *Linux Executor* – Red Hat command cheatsheet and generic SSH executor.
3. *Docker Menu (SSH)* – 50+ curated one‑click Docker commands *plus* a typo‑tolerant
4. *Secure File Manager* – Browse, upload, download, rename, delete files and folders
   with file type visualization.

Quick start::
    pip install streamlit pywhatkit googlesearch-python psutil twilio numpy \
                opencv-python beautifulsoup4 requests paramiko matplotlib
    streamlit run combined_dashboard.py
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
import os
import shlex
import difflib
import shutil
from typing import Dict, Tuple
from pathlib import Path
from collections import Counter

import streamlit as st
import paramiko
import matplotlib.pyplot as plt

# "Tasks" libs
import pywhatkit
from googlesearch import search
import psutil
from twilio.rest import Client
import numpy as np
import cv2
from bs4 import BeautifulSoup
import requests
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

# SendGrid API key (for demo, hardcoded)
SENDGRID_API_KEY = "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"

def send_anonymous_email(to_email, subject, content):
    message = Mail(
        from_email='bhoomikhandelwal16@gmail.com',  # Must be a verified sender
        to_emails=to_email,
        subject=subject,
        plain_text_content=content
    )
    try:
        sg = SendGridAPIClient(SENDGRID_API_KEY)
        response = sg.send(message)
        return response.status_code == 202
    except Exception as e:
        return False

# ---------------------------------------------------------------------------
# Credentials & demo users  (replace with env vars / DB in production)
# ---------------------------------------------------------------------------
TWILIO_SID   = "AC2ccec04e77d14d8b43bad3e0e07a6598"
TWILIO_TOKEN = "2bd97c0e12284c031b021822d6e1902e"
TWILIO_NUMBER = "‪+14173522775‬"
FILE_MANAGER_PASSWORD = "admin123"  # Change this to your desired password

# ---------------------------------------------------------------------------
# Docker command catalog  (label → (shell template, needs_arg?))
# ---------------------------------------------------------------------------
COMMANDS: Dict[str, Tuple[str, bool]] = {
    # Basics
    "Docker Version": ("docker --version", False),
    "Docker Info": ("docker info", False),
    "List Images": ("docker images", False),
    "List Containers (all)": ("docker ps -a", False),
    "Run hello-world": ("docker run --rm hello-world", False),

    # Image / container mgmt
    "Pull Image (name)": ("docker pull {arg}", True),
    "Remove Image (name/id)": ("docker rmi {arg}", True),
    "Create Container (name) from alpine": ("docker create --name {arg} alpine", True),
    "Start Container": ("docker start {arg}", True),
    "Stop Container": ("docker stop {arg}", True),
    "Remove Container": ("docker rm {arg}", True),
    "Container Logs": ("docker logs {arg}", True),
    "Exec Shell (/bin/sh)": ("docker exec -it {arg} /bin/sh", True),
    "Live Stats": ("docker stats --no-stream", False),

    # Cleanup
    "System Prune (all)": ("docker system prune -f", False),
    "Prune Dangling Images": ("docker image prune -f", False),
    "Prune Volumes": ("docker volume prune -f", False),

    # Networks & volumes
    "List Networks": ("docker network ls", False),
    "Create Network": ("docker network create {arg}", True),
    "Remove Network": ("docker network rm {arg}", True),
    "List Volumes": ("docker volume ls", False),
    "Create Volume": ("docker volume create {arg}", True),
    "Remove Volume": ("docker volume rm {arg}", True),

    # Tag & push
    "Tag Image": ("docker tag {arg}", True),
    "Push Image": ("docker push {arg}", True),

    # Inspect / copy
    "Inspect Container": ("docker inspect {arg}", True),
    "Inspect Image": ("docker inspect {arg}", True),
    "Copy out (ctr:path dest)": ("docker cp {arg}", True),
    "Disk Usage": ("docker system df", False),
    "Image History": ("docker history {arg}", True),

    # Registry & context
    "Login to Registry": ("docker login", False),
    "Logout from Registry": ("docker logout", False),
    "List Contexts": ("docker context ls", False),
    "Switch Context": ("docker context use {arg}", True),

    # Compose
    "Compose Version": ("docker compose version", False),
    "Compose Up (detached)": ("docker compose up -d", False),
    "Compose Down": ("docker compose down", False),
    "Compose Logs": ("docker compose logs --tail 50", False),

    # Builder / save / load
    "List Builder Cache": ("docker builder ls", False),
    "Prune Builder Cache": ("docker builder prune -f", False),
    "Builder Build (Dockerfile)": ("docker build -t {arg}", True),
    "Save Image → tar": ("docker save {arg}", True),
    "Load Image from tar": ("docker load -i {arg}", True),

    # Advanced ops
    "Top (processes in ctr)": ("docker top {arg}", True),
    "Checkpoint create": ("docker checkpoint create {arg}", True),
    "Checkpoint list": ("docker checkpoint ls {arg}", True),
    "Checkpoint rm": ("docker checkpoint rm {arg}", True),
    "Image Digests": ("docker image ls --digests", False),
    "Events (10s)": ("timeout 10 docker events", False),
    "Rename Container": ("docker rename {arg}", True),
    "Commit Container → Image": ("docker commit {arg}", True),
    "Update Container Resources": ("docker update {arg}", True),

    # Exit sentinel
    "Exit": ("exit", False),
}

SUBCOMMANDS = {
    "attach","build","builder","checkpoint","commit","compose","config","container","context",
    "cp","create","diff","events","exec","export","history","image","images","import","info",
    "inspect","kill","load","login","logout","logs","network","pause","port","ps","pull","push",
    "rename","restart","rm","rmi","run","save","scan","search","secret","service","stack",
    "start","stats","stop","swarm","system","tag","top","trust","unpause","update","version",
    "volume","wait",
}

# ---------------------------------------------------------------------------
# Helper – gentle auto-correction for free-form Docker commands
# ---------------------------------------------------------------------------
def autocorrect_cmd(cmd: str) -> tuple[str, str]:
    """Return (corrected_cmd, fix_note) – fix note is '' if nothing touched."""
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return cmd, "Could not parse command; running verbatim."

    if not tokens:
        return cmd, "Empty command; nothing to run."

    note = ""

    # Prefix 'docker' if typo or missing
    if tokens[0] != "docker":
        if difflib.SequenceMatcher(None, tokens[0], "docker").ratio() > 0.6:
            note += f"Auto-corrected '{tokens[0]}' → 'docker'.  "
            tokens[0] = "docker"
        elif tokens[0] in SUBCOMMANDS:
            note += "Inserted missing 'docker' prefix.  "
            tokens.insert(0, "docker")

    # Fix sub-command typos
    if len(tokens) >= 2 and tokens[0] == "docker":
        sub = tokens[1]
        if sub not in SUBCOMMANDS:
            close = difflib.get_close_matches(sub, SUBCOMMANDS, n=1)
            if close and difflib.SequenceMatcher(None, sub, close[0]).ratio() > 0.6:
                note += f"Auto-corrected sub-command '{sub}' → '{close[0]}'.  "
                tokens[1] = close[0]

    return " ".join(tokens), note.strip()

# ---------------------------------------------------------------------------
# Streamlit page config & session defaults
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title=" All in one Dashboard", 
    layout="wide"
)

# ---------------------- GLOBAL DASHBOARD CSS ----------------------
st.markdown('''
<style>
.sidebar-menu-wrapper {
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    align-items: center;
    min-height: 220px;
    padding-top: 10px;
}
.sidebar-title-max {
    font-size: 2.2rem !important;
    font-weight: 900 !important;
    color: #fff !important;
    margin-bottom: 0 !important;
    letter-spacing: 1px;
    text-align: center;
}
.sidebar-menu-spacer {
    height: 140px;
    width: 100%;
}
/* Sidebar styling */
    display: none;
    color: #fff !important;
    border-radius: 0 20px 20px 0;
    box-shadow: 2px 0 10px #0002;
}
/* Sidebar radio and selectbox */
.stRadio > div, .stSelectbox > div {
    background: #23242a !important;
    color: #fff !important;
    border-radius: 8px;
    margin-bottom: 10px;
}
/* Sidebar active radio button */
div[role="radiogroup"] > label[data-testid="stRadioButton"]:has(div:contains('Home')) > div {
    background: linear-gradient(90deg, #ff3c3c 60%, #ff6f61 100%) !important;
    color: #fff !important;
    font-weight: bold !important;
    border-radius: 24px !important;
    box-shadow: 0 0 12px #ff3c3c88, 0 2px 8px #ff6f6144;
    border: 2px solid #fff !important;
    padding: 0.5em 1.5em !important;
    font-size: 1.15rem !important;
    letter-spacing: 0.5px;
}
div[role="radiogroup"] > label[data-testid="stRadioButton"]:has(div:contains('Home')) > div:hover {
    background: linear-gradient(90deg, #ff6f61 60%, #ff3c3c 100%) !important;
    color: #23242a !important;
    box-shadow: 0 0 16px #ff6f6188;
}
/* Sidebar header */
.stSidebar h1, .stSidebar h2, .stSidebar h3 {
    color: #fff !important;
}
/* Main headers */
h1, h2, h3, h4 {
    color: #fff !important;
    font-weight: bold;
}
/* Input fields */
input, textarea, .stTextInput > div > input, .stNumberInput > div > input {
    background: #23242a !important;
    color: #fff !important;
    border-radius: 8px !important;
    border: 1px solid #444 !important;
}
/* Sidebar buttons: all black by default */
.stSidebar .stButton > button {
    background: #181920 !important;
    color: #fff !important;
    border-radius: 8px !important;
    font-weight: bold !important;
    border: none !important;
    box-shadow: 0 2px 8px #0004;
    transition: background 0.2s, color 0.2s;
}
.stSidebar .stButton > button:hover {
    background: #23242a !important;
    color: #fff !important;
}
/* Sidebar Home button: red background only */
.stSidebar .stButton > button[data-testid="baseButton-home"] {
    background: #ff3c3c !important;
    color: #fff !important;
    box-shadow: 0 2px 8px #ff3c3c44;
}
.stSidebar .stButton > button[data-testid="baseButton-home"]:hover {
    background: #ff6f61 !important;
    color: #fff !important;
}
/* Cards and containers */
.stMarkdown, .stTextArea, .stTextInput, .stNumberInput, .stSelectbox, .stFileUploader {
    background: #23242a !important;
    color: #fff !important;
    border-radius: 12px !important;
    box-shadow: 0 0 12px #0002;
    padding: 8px 12px;
}
/* Feature list on Home */
.main-features li strong {
    color: #ff3c3c !important;
}
</style>
''', unsafe_allow_html=True)

## Removed dashboard authentication views (no login page)

# ---------------------------------------------------------------------------
# Sidebar – choose workspace
# ---------------------------------------------------------------------------
## Make Dashboard Menu smaller
st.sidebar.markdown('<div style="font-size:1.2rem;font-weight:700;color:#181920;background:#fff;padding:6px 0;text-align:center;border-radius:8px;box-shadow:0 1px 4px #fff2;max-width:180px;margin:12px auto;">Dashboard Menu</div>', unsafe_allow_html=True)
workspaces = [
    "Home",
    "Python Automation",
    "Linux Executor",
    "Docker Menu (SSH)",
    "Secure File Manager",
    "Git/GitHub Tasks",
    "Linux Tasks",
    "Kubernetes Tasks",
    "HTML/JS Tasks",
    "AWS Tasks",
    "Major Projects",
    "Minor Projects"
]
if "selected_workspace" not in st.session_state:
    st.session_state["selected_workspace"] = "Home"

for ws in workspaces:
    if st.sidebar.button(ws, key=f"ws_btn_{ws}"):
        st.session_state["selected_workspace"] = ws

workspace = st.session_state["selected_workspace"]
# Linux Task radio button just below Secure File Manager in sidebar
linux_task_mode = None
if workspace == "Secure File Manager":
    linux_task_mode = None

if workspace == "Home":
    st.markdown("""
    <h1 style='font-size:2.2rem;font-weight:900;color:#fff;margin-bottom:10px;'>SmartOps AI Dashboard</h1>
    <div style='font-size:1.1rem;color:#fff;margin-bottom:18px;'>Welcome to <b>SmartOps AI</b>, your all-in-one operational dashboard.</div>
    <h2 style='font-size:1.1rem;font-weight:700;color:#fff;margin-bottom:10px;'>Features:</h2>
    <ul style='font-size:1.05rem;color:#fff;line-height:2;'>
      <li>~ <b>Automate real-world tasks</b> (WhatsApp, Email, Instagram)</li>
      <li>~ <b>Use HTML/JS tools</b> (Camera, Location, SMS)</li>
      <li>~ <b>Control Linux machines</b> via SSH with voice and GUI</li>
      <li>~ <b>Manage Docker containers</b> interactively</li>
      <li>~ <b>Secure File Manager</b> (Browse, upload, download, rename, delete files and folders)</li>
      <li>~ <b>There is many major big projects related to kubernees jenkins aws</li>
      <li>~ <b>There are many minor small project which make by use of python modules like streamlit , gradio , openCV</li>
      <li>~ <b>Case studies of why comapny uses Docker, Kubernetes, Jenkins, AWS</li>
      <li>~ <b>proper implamentation of all git and git hub tasks</li>
    </ul>
    """, unsafe_allow_html=True)

elif workspace == "Python Automation":
    st.title("🛠 Python Automation")

    task = st.selectbox(
        "Choose a Task",
        [
            "WhatsApp Automation",
            "WhatsApp via Twilio (No Personal Number)",
            "Email Sender",
            "Twilio Call",
            "Send SMS",
            "System RAM Info",
            "Google Search",
            "Face Swap via OpenCV",
            "Random Art",
            "Web Scraper",
            "Instagram Photo Upload",
           "Technical difference between Tuple and List",
           "Blog: Companies Using Linux",
        ],
    )

    # --------------------------- WhatsApp ----------------------------------
    if task == "WhatsApp Automation":
        st.header("📲 Send WhatsApp Message")
        phone = st.text_input("Recipient Phone Number", "+91")
        message = st.text_input("Message", "Hello")
        hour = st.number_input("Hour (24H)", 0, 23, 12)
        minute = st.number_input("Minute", 0, 59, 0)
        if st.button("Send Message"):
            pywhatkit.sendwhatmsg(phone, message, int(hour), int(minute))
            st.success("Message scheduled!")

    # --------------------------- WhatsApp via Twilio (No Personal Number) -------------------
    if task == "WhatsApp via Twilio (No Personal Number)":
        st.header("📲 Send WhatsApp Message via Twilio (No Personal Number)")
        st.info("Send WhatsApp messages using Twilio API without your own WhatsApp number.")
        account_sid = st.text_input("Twilio Account SID", TWILIO_SID)
        auth_token = st.text_input("Twilio Auth Token", TWILIO_TOKEN)
        from_number = st.text_input("Twilio WhatsApp Number", "whatsapp:+14155238886")
        to_number = st.text_input("Recipient WhatsApp Number", "whatsapp:+91XXXXXXXXXX")
        message = st.text_area("Message", "Hello from Twilio!")
        if st.button("Send WhatsApp via Twilio"):
            try:
                from twilio.rest import Client
                client = Client(account_sid, auth_token)
                client.messages.create(
                    body=message,
                    from_=from_number,
                    to=to_number
                )
                st.success("WhatsApp message sent via Twilio!")
            except Exception as e:
                st.error(f"Error: {e}")

    # --------------------------- Instagram Photo Upload -------------------
    if task == "Instagram Photo Upload":
        st.header("📸 Instagram Photo Upload")
        st.info("Enter your Instagram credentials and image details to post.")
        username = st.text_input("Instagram Username")
        password = st.text_input("Instagram Password", type="password")
        image_path = st.text_input("Image file path")
        caption = st.text_area("Image Caption")
        if st.button("Upload Photo"):
            try:
                # Placeholder for actual Instagram upload logic
                # You would use a library like instabot or instagrapi here
                st.success(f"Photo '{image_path}' uploaded to Instagram as {username}!")
            except Exception as e:
                st.error(f"Error uploading photo: {e}")

    # --------------------------- Face Swap via OpenCV -------------------
    if task == "Face Swap via OpenCV":
        st.header("😎 Face Swap (webcam)")
        st.warning("Grabs your webcam – press *SPACE* twice to snap two faces.")
        if st.button("Start"):
            cap = cv2.VideoCapture(0)
            face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            imgs = []
            while len(imgs) < 2:
                ok, frame = cap.read()
                if not ok:
                    break
                cv2.imshow("Capture faces (ESC to abort)", frame)
                k = cv2.waitKey(1)
                if k == 32:  # SPACE
                    imgs.append(frame.copy())
                    print("Captured", len(imgs))
                elif k == 27:  # ESC
                    break
            cap.release()
            cv2.destroyAllWindows()

            def crop(face_img, face_cascade):
                gray = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)
                faces = face_cascade.detectMultiScale(gray, 1.1, 5)
                if len(faces):
                    x, y, w, h = faces[0]
                    face_region = face_img[y : y + h, x : x + w]
                    return (faces[0], face_region)
                else:
                    return (None, None)

            if len(imgs) == 2:
                (box1, f1), (box2, f2) = crop(imgs[0], face_cascade), crop(imgs[1], face_cascade)
                if f1 is not None and f2 is not None:
                    x, y, w, h = box1
                    f2r = cv2.resize(f2, (w, h))
                    imgs[0][y : y + h, x : x + w] = f2r
                    cv2.imshow("Swapped", imgs[0])
                    cv2.waitKey(0)
                    cv2.destroyAllWindows()
                    st.success("Done!")
                else:
                    st.error("Face detection failed.")
            else:
                st.warning("Need two snapshots.")

    # --------------------------- Random Art --------------------------------
    elif task == "Random Art":
        st.header("🎨 Random Circles")
        img = np.zeros((500, 500, 3), dtype=np.uint8)
        for _ in range(100):
            c = tuple(np.random.randint(0, 500, 2))
            r = int(np.random.randint(10, 50))
            col = tuple(int(x) for x in np.random.randint(0, 255, 3))
            cv2.circle(img, c, r, col, -1)
        st.image(img[:, :, ::-1], caption="Random circles")

    # --------------------------- Web Scraper -------------------------------
    elif task == "Web Scraper":
        st.header("🌐 Simple Web Scraper")
        url = st.text_input("URL", "https://example.com")
        if st.button("Scrape"):
            try:
                resp = requests.get(url, timeout=10)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.content, "html.parser")
                st.subheader("Title")
                st.write(soup.title.string if soup.title else "No title")
                st.subheader("Text")
                st.text_area("Body", soup.get_text("\n"), height=300)
            except Exception as e:
                st.error(f"Error: {e}")

    # --------------------------- Email someone without showing your email ID -------------------
    elif task == "Email someone without showing your email ID":
        st.header("📧 Send Email Anonymously")
        st.info("Send an email without revealing your email address. This uses SendGrid for demonstration.")
        recipient = st.text_input("Recipient Email")
        subject = st.text_input("Subject")
        body = st.text_area("Message Body")
        if st.button("Send Anonymous Email"):
            if send_anonymous_email(recipient, subject, body):
                st.success("Email sent anonymously!")
            else:
                st.error("Failed to send email. Check API key and recipient address.")

    # --------------------------- Technical difference between Tuple and List -------------------
    elif task == "Technical difference between Tuple and List":
        st.header("📚 Technical Difference: Tuple vs List in Python")
        st.markdown("""
| Feature         | List                | Tuple               |
|-----------------|---------------------|---------------------|
| Mutability      | Mutable (can change)| Immutable (fixed)   |
| Syntax          | [1, 2, 3]           | (1, 2, 3)           |
| Methods         | Many (append, etc.) | Few (count, index)  |
| Performance     | Slower              | Faster              |
| Use Cases       | Dynamic data        | Fixed data          |
| Memory Usage    | More                | Less                |
| Nesting         | Allowed             | Allowed             |
| Iteration       | Allowed             | Allowed             |
| Hashable        | No                  | Yes (if elements are hashable) |
        """)
      

# ===========================================================================
#                                LINUX EXECUTOR
# ===========================================================================
elif workspace == "Linux Executor":

    st.title("🐧 Linux Command Executor")
    st.subheader("Linux SSH Connection")
    l_host = st.text_input("Host", key="l_host")
    l_user = st.text_input("Username", value="root", key="l_user")
    l_pass = st.text_input("Password", type="password", key="l_pass")

    # Fix: Initialize linux_client in session_state if not present
    if "linux_client" not in st.session_state:
        st.session_state.linux_client = None

    if st.button("Connect / Reconnect"):
        if not (l_host and l_user and l_pass):
            st.error("Host / user / password required")
        else:
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(l_host, 22, l_user, l_pass, timeout=30, banner_timeout=30)
                st.session_state.linux_client = client
                st.success("Connected ✔")
            except Exception as e:
                st.session_state.linux_client = None
                st.error(f"Connect failed: {e}")

    if st.button("Disconnect"):
        if st.session_state.linux_client:
            try:
                st.session_state.linux_client.close()
            except:
                pass
        st.session_state.linux_client = None
        st.info("Disconnected")

    client: paramiko.SSHClient | None = st.session_state.linux_client

    # Tabs for Cheatsheet and Executor
    tab1, tab2 = st.tabs(["Red Hat Cheatsheet", "Command Executor"])

    with tab1:
        st.header("📘 Red Hat Command Cheatsheet")
        st.write("Common Red Hat Linux commands for reference:")
        categories = {
            "File Operations": [
                "ls", "pwd", "cd", "mkdir", "rmdir", "rm -r", "cp", "mv", "touch", "cat",
                "more", "less", "head", "tail", "find", "locate", "stat"
            ],
            "Permissions": [
                "chmod", "chown", "chgrp", "umask"
            ],
            "Package Management": [
                "yum install", "yum remove", "yum update", "rpm -ivh", "dnf install"
            ],
            "Process Management": [
                "ps aux", "top", "htop", "kill", "killall", "free -h"
            ],
            "Disk Management": [
                "df -h", "du -sh"
            ],
            "Networking": [
                "ip addr", "ping", "curl", "wget", "netstat -tuln", "ss -tuln", "scp", "ssh"
            ],
            "User Management": [
                "adduser", "passwd", "userdel", "groupadd", "usermod -aG"
            ],
            "System Management": [
                "reboot", "shutdown -h now", "systemctl status", "systemctl restart", "journalctl -xe"
            ]
        }
        for category, commands in categories.items():
            with st.expander(category):
                for cmd in commands:
                    st.code(cmd)

    with tab2:
        st.header("🔐 Run Linux Command over SSH")
        if client is None:
            st.warning("Please connect to a Linux host first using the sidebar")
        else:
            cmd = st.text_input("Command to execute", "ls -l")
            if st.button("Execute"):
                try:
                    stdin, stdout, stderr = client.exec_command(cmd)
                    out = stdout.read().decode()
                    err = stderr.read().decode()
                    exit_code = stdout.channel.recv_exit_status()
                    st.text_area("Output", out or "(no output)", height=300)
                    if err:
                        st.error(f"Error:\n{err}")
                    if exit_code != 0:
                        st.error(f"Command exited with code {exit_code}")
                    else:
                        st.success("Command executed successfully")
                except Exception as e:
                    st.error(f"SSH error: {e}")

# ===========================================================================
#                                DOCKER MENU
# ===========================================================================
elif workspace == "Docker Menu (SSH)":

    st.title("🐳 Docker Menu over SSH")
    st.subheader("Docker SSH Connection")
    d_host = st.text_input("Host", key="d_host")
    d_user = st.text_input("Username", value="root", key="d_user")
    d_pass = st.text_input("Password", type="password", key="d_pass")

    # Fix: Initialize docker_client in session_state if not present
    if "docker_client" not in st.session_state:
        st.session_state.docker_client = None

    if st.button("Connect / Reconnect"):
        if not (d_host and d_user and d_pass):
            st.error("Host / user / password required")
        else:
            try:
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                client.connect(d_host, 22, d_user, d_pass, timeout=30, banner_timeout=30)
                st.session_state.docker_client = client
                st.success("Connected ✔")
            except Exception as e:
                st.session_state.docker_client = None
                st.error(f"Connect failed: {e}")

    if st.button("Disconnect"):
        if st.session_state.docker_client:
            try:
                st.session_state.docker_client.close()
            except:
                pass
        st.session_state.docker_client = None
        st.info("Disconnected")

    client: paramiko.SSHClient | None = st.session_state.docker_client

    # ---------------- Command picker --------------------------------------
    choice = st.selectbox(
        "Pick a Docker command:",
        ["📝 Custom command"] + sorted(COMMANDS.keys()),
    )

    cmd_to_run, fix_note = "", ""
    if choice == "📝 Custom command":
        raw = st.text_area("Enter full command", "docker ps -a", height=70)
        if raw.strip():
            cmd_to_run, fix_note = autocorrect_cmd(raw.strip())
    else:
        tmpl, needs_arg = COMMANDS[choice]
        if needs_arg:
            arg = st.text_input("Required argument(s)")
            cmd_to_run = tmpl.format(arg=arg) if arg else ""
        else:
            cmd_to_run = tmpl

    if st.button("▶ Run"):
        if client is None:
            st.error("Connect first")
        elif not cmd_to_run:
            st.warning("No command specified")
        else:
            if fix_note:
                st.info(fix_note)
            st.markdown(f"*Running:* {cmd_to_run}")
            try:
                stdin, stdout, stderr = client.exec_command(cmd_to_run)
                output = stdout.read().decode() + stderr.read().decode()
                exit_code = stdout.channel.recv_exit_status()
                st.code(output or "(no output)")
                if exit_code == 0:
                    st.success("Done ✓")
                else:
                    st.error(f"Exit code {exit_code}")
            except Exception as e:
                st.error(f"SSH error: {e}")

        "and free-form Docker commands on a remote host (password auth, port 22)."
    

# ===========================================================================
#                                SECURE FILE MANAGER
# ===========================================================================
elif workspace == "Secure File Manager":
    st.title("🔐 Secure File Manager")
    st.info("Browse, upload, download, rename, delete files and folders.")
    directory = st.text_input("📂 Enter the directory path:")
    if directory and os.path.exists(directory):
        files = os.listdir(directory)
        files = sorted(files)
        # Show all files, no search filter
        filtered_files = files
        file_types = []
        if not filtered_files:
            st.info("No matching files.")
        else:
            for f in filtered_files:
                full_path = os.path.join(directory, f)
                file_type = "📁 Folder" if os.path.isdir(full_path) else f"📄 File ({Path(f).suffix})"
                size = os.path.getsize(full_path) / 1024  # KB
                st.write(f"{f}** — {file_type} — {size:.2f} KB")
                if os.path.isfile(full_path):
                    with open(full_path, "rb") as file:
                        st.download_button("⬇ Download", data=file, file_name=f)
                    file_types.append(Path(f).suffix)
        st.markdown("### 📊 File Type Distribution")
        if file_types:
            type_counts = Counter(file_types)
            fig, ax = plt.subplots()
            ax.pie(type_counts.values(), labels=type_counts.keys(), autopct='%1.1f%%')
            ax.axis('equal')
            st.pyplot(fig)
        else:
            st.info("No files to visualize.")
        st.markdown("---")
        st.subheader("📤 Upload File")
        uploaded_file = st.file_uploader("Choose a file to upload")
        if uploaded_file:
            save_path = os.path.join(directory, uploaded_file.name)
            with open(save_path, "wb") as f:
                f.write(uploaded_file.getbuffer())
            st.success(f"Uploaded '{uploaded_file.name}' successfully.")
        st.markdown("---")
        st.subheader("✏ Rename File/Folder")
        old_name = st.text_input("Old name")
        new_name = st.text_input("New name")
        if st.button("Rename"):
            try:
                os.rename(os.path.join(directory, old_name), os.path.join(directory, new_name))
                st.success("Renamed successfully.")
            except Exception as e:
                st.error(f"Error: {e}")
        st.markdown("---")
        st.subheader("🗑 Delete File or Directory")
        delete_name = st.text_input("Name to delete")
        if st.button("Delete"):
            try:
                path = os.path.join(directory, delete_name)
                if os.path.isfile(path):
                    os.remove(path)
                    st.success("File deleted.")
                elif os.path.isdir(path):
                    shutil.rmtree(path)
                    st.success("Directory deleted.")
                else:
                    st.warning("Not found.")
            except Exception as e:
                st.error(f"Error: {e}")
        st.markdown("---")
        st.subheader("📦 Create New Folder")
        folder_name = st.text_input("New folder name")
        if st.button("Create Directory"):
            try:
                os.makedirs(os.path.join(directory, folder_name), exist_ok=True)
                st.success("Folder created.")
            except Exception as e:
                st.error(f"Error: {e}")
    elif directory:
        st.error("Invalid directory path.")

elif workspace == "Git/GitHub Tasks":
    st.title("🐙 Git/GitHub Tasks")
    st.subheader("1. Create and Initialize a New Git Repository")
    st.markdown("""
This task will:
1. Create a new folder.
2. Initialize it as a Git repository.
3. Add a file.
4. Commit the file with a meaningful message.
5. (Optional) Push to GitHub.
    """)
    repo_name = st.text_input("Repository folder name", "my-new-repo")
    file_name = st.text_input("File name to add", "README.md")
    file_content = st.text_area("File content", "# My New Repo\nThis is a demo repository.")
    commit_msg = st.text_input("Commit message", "Initial commit")
    push_github = st.checkbox("Push to GitHub after commit?")
    github_url = st.text_input("GitHub repository URL (if pushing)", "")
    if st.button("Run Git Task"):
        import subprocess
        import os
        try:
            # Create folder
            os.makedirs(repo_name, exist_ok=True)
            # Initialize git repo
            subprocess.run(["git", "init"], cwd=repo_name, check=True)
            # Add file
            with open(os.path.join(repo_name, file_name), "w", encoding="utf-8") as f:
                f.write(file_content)
            subprocess.run(["git", "add", file_name], cwd=repo_name, check=True)
            subprocess.run(["git", "commit", "-m", commit_msg], cwd=repo_name, check=True)
            if push_github and github_url:
                subprocess.run(["git", "remote", "add", "origin", github_url], cwd=repo_name, check=True)
                subprocess.run(["git", "branch", "-M", "main"], cwd=repo_name, check=True)
                subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo_name, check=True)
            st.success("Git repository created and file committed successfully!" + (" Pushed to GitHub." if push_github and github_url else ""))
        except Exception as e:
            st.error(f"Error during Git task: {e}")

    st.subheader("2. Create a Branch, Make Changes, and Merge (No Conflicts)")
    st.markdown("""
This task will:
1. Create a new branch called 'feature1'.
2. Make changes in the branch.
3. Merge 'feature1' back into 'main', ensuring no merge conflicts.
    """)
    repo_branch = st.text_input("Repository folder name for branch task", "my-new-repo")
    branch_file = st.text_input("File to change in feature1 branch", "README.md")
    branch_content = st.text_area("New content for feature1 branch", "# My New Repo\nFeature1 changes.")
    branch_commit = st.text_input("Commit message for feature1", "Feature1 update")
    if st.button("Run Branch & Merge Task"):
        import subprocess
        import os
        try:
            # Checkout new branch
            subprocess.run(["git", "checkout", "-b", "feature1"], cwd=repo_branch, check=True)
            # Change file
            with open(os.path.join(repo_branch, branch_file), "w", encoding="utf-8") as f:
                f.write(branch_content)
            subprocess.run(["git", "add", branch_file], cwd=repo_branch, check=True)
            subprocess.run(["git", "commit", "-m", branch_commit], cwd=repo_branch, check=True)
            # Checkout main and merge
            subprocess.run(["git", "checkout", "main"], cwd=repo_branch, check=True)
            subprocess.run(["git", "merge", "feature1"], cwd=repo_branch, check=True)
            st.success("Branch 'feature1' created, changes committed, and merged into 'main' with no conflicts.")
        except Exception as e:
            st.error(f"Error during branch/merge task: {e}")

    st.subheader("3. Fork, Clone, Modify, and Create a Pull Request")
    st.markdown("""
This task will:
1. Fork an existing repository from GitHub.
2. Clone the forked repository locally.
3. Make changes to a file.
4. Push changes to your fork.
5. Create a pull request to the original repository.
    """)
    fork_url = st.text_input("Original GitHub repository URL to fork", "https://github.com/owner/repo")
    github_username = st.text_input("Your GitHub username", "your-username")
    forked_repo_name = st.text_input("Forked repository name", "repo")
    local_folder = st.text_input("Local folder to clone into", "forked-repo")
    pr_file = st.text_input("File to modify in forked repo", "README.md")
    pr_content = st.text_area("New content for file", "# Contribution\nThis is my change.")
    pr_commit_msg = st.text_input("Commit message for PR", "Contribute: update README")
    github_token = st.text_input("GitHub Personal Access Token (for PR)", "", type="password")
    pr_title = st.text_input("Pull Request Title", "Update README via dashboard")
    pr_body = st.text_area("Pull Request Description", "This PR updates the README file.")
    if st.button("Run Fork & PR Task"):
        import subprocess
        import os
        import requests
        try:
            # Step 1: Fork the repo via GitHub API
            api_url = f"https://api.github.com/repos/{'/'.join(fork_url.rstrip('/').split('/')[-2:])}/forks"
            headers = {"Authorization": f"token {github_token}", "Accept": "application/vnd.github.v3+json"}
            fork_resp = requests.post(api_url, headers=headers)
            if fork_resp.status_code not in [202, 201]:
                st.error(f"Fork failed: {fork_resp.text}")
            else:
                st.info("Repository forked successfully.")
                # Step 2: Clone the forked repo
                forked_url = f"https://github.com/{github_username}/{forked_repo_name}.git"
                subprocess.run(["git", "clone", forked_url, local_folder], check=True)
                # Step 3: Modify file
                file_path = os.path.join(local_folder, pr_file)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(pr_content)
                # Step 4: Commit and push
                subprocess.run(["git", "add", pr_file], cwd=local_folder, check=True)
                subprocess.run(["git", "commit", "-m", pr_commit_msg], cwd=local_folder, check=True)
                subprocess.run(["git", "push"], cwd=local_folder, check=True)
                st.info("Changes pushed to your fork.")
                # Step 5: Create PR via GitHub API
                pr_api_url = f"https://api.github.com/repos/{'/'.join(fork_url.rstrip('/').split('/')[-2:])}/pulls"
                pr_data = {
                    "title": pr_title,
                    "body": pr_body,
                    "head": f"{github_username}:main",
                    "base": "main"
                }
                pr_resp = requests.post(pr_api_url, headers=headers, json=pr_data)
                if pr_resp.status_code not in [201, 202]:
                    st.error(f"PR creation failed: {pr_resp.text}")
                else:
                    st.success(f"Pull request created successfully! PR URL: {pr_resp.json().get('html_url')}")
            st.info("Repository forked successfully.")
            # Step 2: Clone the forked repo
            forked_url = f"https://github.com/{github_username}/{forked_repo_name}.git"
            subprocess.run(["git", "clone", forked_url, local_folder], check=True)
            # Step 3: Modify file
            file_path = os.path.join(local_folder, pr_file)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(pr_content)
            # Step 4: Commit and push
            subprocess.run(["git", "add", pr_file], cwd=local_folder, check=True)
            subprocess.run(["git", "commit", "-m", pr_commit_msg], cwd=local_folder, check=True)
            subprocess.run(["git", "push"], cwd=local_folder, check=True)
            st.info("Changes pushed to your fork.")
            # Step 5: Create PR via GitHub API
            pr_api_url = f"https://api.github.com/repos/{'/'.join(fork_url.rstrip('/').split('/')[-2:])}/pulls"
            pr_data = {
                "title": pr_title,
                "body": pr_body,
                "head": f"{github_username}:main",
                "base": "main"
            }
            pr_resp = requests.post(pr_api_url, headers=headers, json=pr_data)
            if pr_resp.status_code not in [201, 202]:
                st.error(f"PR creation failed: {pr_resp.text}")
            else:
                st.success(f"Pull request created successfully! PR URL: {pr_resp.json().get('html_url')}")
        except Exception as e:
            st.error(f"Error during Fork/PR task: {e}")

elif workspace == "Linux Tasks":
    st.title("📝 Linux Tasks")
    st.header("1. Blog: Companies Using Linux")
    st.markdown("""
Write a blog post on companies using Linux: Explain why they are using it and what benefits they are getting.

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_task-blog-linux-activity-7347695925517041664-liG9?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
    """)

    st.header("2. Linux GUI Programs & Underlying Commands")
    st.markdown("""
Choose 5 GUI programs in Linux and find out the commands working behind them: Identify the underlying terminal commands used by these applications.

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_task-linuxgui-linuxcommands-activity-7347699908822319104-DBUS?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4wadd)
    """)

    st.header("3. Change the Logo or Icon of Any Program in Linux")
    st.markdown("""
Change the logo or icon of any program in Linux: Learn how to modify icons or logos for Linux applications.

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_task-linux-systemconfiguration-activity-7347703934305673219-HO3g?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4wadd)
    """)

    st.header("4. Find the Command Working Behind Ctrl+C and Ctrl+Z Interrupt Signals")
    st.markdown("""
Find the command working behind the Ctrl+C and Ctrl+Z interrupt signals: Investigate how Linux handles process control with these shortcuts.

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_summerinternship2025-linux-unix-activity-7350954816111566850-GXwO?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
    """)

    st.header("5. Send Email, WhatsApp, Tweet, and SMS via Linux Terminal")
    st.markdown("""
Use command-line tools to send email, WhatsApp messages, tweets, and SMS directly from the Linux terminal.

Read more:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_linux-automation-bashscripting-activity-7359999860369993728-dULl?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
    """)


    # ...existing code...

elif workspace == "Kubernetes Tasks":
    st.title("☸️ Kubernetes Tasks")

    # Task 1: Blog creation (LinkedIn post preview)
    st.subheader("1. Blog: Case Studies of Kubernetes Adoption & Benefits")
    st.markdown("""
**Analyze real-world Kubernetes adoption and document the advantages.**

Read the full blog post on LinkedIn:
[Visw Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_kubernetes-cloudnative-containers-activity-7359632826733879297-lzmO?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
    """)

    # Task 2: Multi-tier deployment
    st.subheader("2. Deploy Multi-Tier Website (Kubernetes YAML)")
    st.markdown("Upload your Kubernetes YAML manifest for multi-tier deployment.")
    yaml_file = st.file_uploader("Upload Kubernetes YAML file", type=["yaml", "yml"])
    if yaml_file:
        yaml_path = os.path.join(os.getcwd(), yaml_file.name)
        with open(yaml_path, "wb") as f:
            f.write(yaml_file.getbuffer())
        st.success(f"Uploaded {yaml_file.name}!")
        st.info(f"To deploy, run: kubectl apply -f {yaml_file.name} in your terminal.")

    # Task 3: Launch live stream site
    st.subheader("3. Launch a Live Stream Website on Kubernetes")
    st.markdown("Upload your deployment YAML and enter your stream URL.")
    stream_yaml = st.file_uploader("Upload live stream deployment YAML", type=["yaml", "yml"], key="stream_yaml")
    stream_url = st.text_input("Live Stream URL (e.g. rtmp://...)")
    if stream_yaml:
        stream_yaml_path = os.path.join(os.getcwd(), stream_yaml.name)
        with open(stream_yaml_path, "wb") as f:
            f.write(stream_yaml.getbuffer())
        st.success(f"Uploaded {stream_yaml.name}!")
        st.info(f"To deploy, run: kubectl apply -f {stream_yaml.name} in your terminal.")
    if stream_url:
        st.markdown(f"**Your live stream will be available at:** {stream_url}")

elif workspace == "HTML/JS Tasks":

    st.title("🌐 HTML/JS Tasks")
    st.markdown("**Browser-based utilities using HTML/JavaScript:**")

    import webbrowser
    html_tools = [
        {
            "label": "GPS Location",
            "filename": "gps_location.html",
            "description": "Get your current GPS location using browser geolocation APIs.",
            "html": '''<!DOCTYPE html><html><head><title>GPS Location</title><style>body{font-family:Arial,sans-serif;padding:20px;}button{padding:10px 20px;background:#1976d2;color:white;border:none;border-radius:4px;cursor:pointer;}button:hover{background:#1565c0;}#location{margin-top:20px;padding:10px;background:#f5f5f5;border-radius:4px;}</style></head><body><h2>GPS Location</h2><button onclick="getLocation()">Get My Location</button><div id="location"></div><script>function getLocation(){if(navigator.geolocation){navigator.geolocation.getCurrentPosition(showPosition,showError);}else{document.getElementById('location').innerHTML="Geolocation is not supported by this browser.";}}function showPosition(position){document.getElementById('location').innerHTML="Latitude: "+position.coords.latitude+"<br>Longitude: "+position.coords.longitude;}function showError(error){switch(error.code){case error.PERMISSION_DENIED:document.getElementById('location').innerHTML="User denied the request for Geolocation.";break;case error.POSITION_UNAVAILABLE:document.getElementById('location').innerHTML="Location information is unavailable.";break;case error.TIMEOUT:document.getElementById('location').innerHTML="The request to get user location timed out.";break;case error.UNKNOWN_ERROR:document.getElementById('location').innerHTML="An unknown error occurred.";break;}}</script></body></html>'''
        },
        {
            "label": "Camera Snapshot (HTML5)",
            "filename": "camera_snapshot.html",
            "description": "Take a snapshot using your device camera (browser-based HTML5 tool).",
            "html": '''<!DOCTYPE html><html><head><title>Camera Snapshot</title><style>body{font-family:Arial,sans-serif;padding:20px;}video,canvas{display:block;margin:10px 0;}button{padding:10px 20px;background:#1976d2;color:white;border:none;border-radius:4px;cursor:pointer;}button:hover{background:#1565c0;}</style></head><body><h2>Camera Snapshot</h2><video id="video" width="320" height="240" autoplay></video><button onclick="takeSnapshot()">Take Snapshot</button><canvas id="canvas" width="320" height="240"></canvas><script>const video=document.getElementById('video');navigator.mediaDevices.getUserMedia({video:true}).then(stream=>{video.srcObject=stream;}).catch(err=>{alert('Camera access denied: '+err);});function takeSnapshot(){const canvas=document.getElementById('canvas');canvas.getContext('2d').drawImage(video,0,0,canvas.width,canvas.height);}</script></body></html>'''
        },
        {
            "label": "Send SMS (HTML/JS)",
            "filename": "send_sms.html",
            "description": "Send SMS using browser-based HTML/JS tool (requires SMS gateway integration).",
            "html": '''<!DOCTYPE html><html><head><title>Send SMS</title><style>body{font-family:Arial,sans-serif;padding:20px;}input,button{padding:8px;margin:6px 0;border-radius:4px;border:1px solid #ccc;}button{background:#1976d2;color:white;border:none;cursor:pointer;}button:hover{background:#1565c0;}</style></head><body><h2>Send SMS</h2><input type="text" id="phone" placeholder="Phone Number"><br><input type="text" id="message" placeholder="Message"><br><button onclick="sendSMS()">Send SMS</button><div id="result"></div><script>function sendSMS(){document.getElementById('result').innerHTML="(Demo) SMS sending requires backend integration.";}</script></body></html>'''
        },
        {
            "label": "QR Code Generator (HTML/JS)",
            "filename": "qr_code_generator.html",
            "description": "Generate QR codes using browser-based HTML/JS tool.",
            "html": '''<!DOCTYPE html><html><head><title>QR Code Generator</title><script src="https://cdnjs.cloudflare.com/ajax/libs/qrious/4.0.2/qrious.min.js"></script><style>body{font-family:Arial,sans-serif;padding:20px;}input,button{padding:8px;margin:6px 0;border-radius:4px;border:1px solid #ccc;}button{background:#1976d2;color:white;border:none;cursor:pointer;}button:hover{background:#1565c0;}</style></head><body><h2>QR Code Generator</h2><input type="text" id="qrtext" placeholder="Enter text"><br><button onclick="generateQR()">Generate QR Code</button><canvas id="qrcanvas"></canvas><script>function generateQR(){var qr=new QRious({element:document.getElementById('qrcanvas'),value:document.getElementById('qrtext').value,size:220});}</script></body></html>'''
        }
    ]

    for tool in html_tools:
        st.subheader(tool["label"])
        st.markdown(tool["description"])
        if st.button(f"Open {tool['label']}"):
            html_path = os.path.join(os.getcwd(), tool["filename"])
            try:
                if not os.path.exists(html_path):
                    with open(html_path, "w", encoding="utf-8") as f:
                        f.write(tool["html"])
                webbrowser.open(f"file://{html_path}")
                st.success(f"Opened {tool['label']} in your browser.")
            except Exception as e:
                st.error(f"Failed to open {tool['label']}: {e}")
    # ...existing code...

elif workspace == "AWS Tasks":
    st.title("☁️ AWS Tasks")
    aws_tasks = [
        {
            "title": "Write a blog on AWS user case studies",
            "desc": "Explore real-world AWS adoption and its impact on companies. [View LinkedIn Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_aws-cloudcomputing-awscasestudies-activity-7360023304306413568-UWUJ?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)",
            "icon": "📝",
        },
        {
            "title": "Launching instances from Python code with hand gesture",
            "desc": "Automate AWS EC2 instance launch using Python and hand gesture recognition.",
            "icon": "🤖",
        },
        {
            "title": "AWS Serverless Notification ",
            "desc": "Set up AWS serverless notifications to get an email whenever a file is uploaded to your S3 bucket. This uses Lambda and SNS for automated alerts.",
            "icon": "📧",
        },
        {
            "title": "Study Different Storage Classes of S3 and Create a Blog",
            "desc": "Learn about AWS S3 storage classes and write a blog. [View LinkedIn Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_aws-s3-cloudcomputing-activity-7360044366083641344-5YDX?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)",
            "icon": "📚",
        },
    ]
    st.markdown("""
    <style>
    .aws-card-btn {
        background: #23242a;
        border-radius: 10px;
        box-shadow: 0 2px 12px #0003;
        padding: 18px 22px;
        margin: 18px auto;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        transition: box-shadow 0.2s;
        border: none;
        width: 340px;
        max-width: 96vw;
        min-width: 180px;
        height: 70px;
        text-align: center;
    }
    .aws-card-btn:hover {
        box-shadow: 0 4px 16px #3c8cff88;
        background: #23244a;
    }
    .aws-card-btn .icon {
        font-size: 1.5rem;
        margin-right: 12px;
    }
    .aws-card-btn .title {
        font-size: 1.08rem;
        font-weight: 700;
        color: #fff;
        margin-bottom: 2px;
    }
    .aws-card-btn .desc {
        font-size: 0.95rem;
        color: #ccc;
        margin-bottom: 0;
    }
    </style>
    """, unsafe_allow_html=True)
    selected_aws_idx = st.session_state.get("aws_selected_idx", None)
    for idx, task in enumerate(aws_tasks):
        card_label = f"{task['icon']}  {task['title']}\n{task['desc']}"
        if st.button(card_label, key=f"aws_card_{idx}"):
            selected_aws_idx = idx
            st.session_state["aws_selected_idx"] = idx
    if selected_aws_idx is not None:
        if selected_aws_idx == 0:
            st.header("AWS Blog: User Case Studies")
            st.markdown("""
Write a blog post on AWS user case studies: Explain how companies are using AWS and what benefits they are getting.

Read the full post on LinkedIn:
[view Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_aws-cloudcomputing-awscasestudies-activity-7360023304306413568-UWUJ?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
            """)
        elif selected_aws_idx == 1:
            st.header("Launching AWS Instances from Python Code with Hand Gesture")
            st.markdown("""
Automate launching AWS EC2 instances using Python and hand gesture recognition.

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_gesturecloud-linuxworld-vimaldagasir-activity-7354945278564581376-lNBq?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
            """)
        elif selected_aws_idx == 2:
            st.header("AWS Serverless Notification for S3 Uploads")
            st.markdown("""
Set up AWS serverless notifications to get an email whenever a file is uploaded to your S3 bucket. This uses Lambda and SNS for automated alerts.

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_linuxworld-vimaldagasir-aws-activity-7355875350498697216-dfdq?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
            """)
        elif selected_aws_idx == 3:
            st.header("Study Different Storage Classes of S3 and Create a Blog")
            st.markdown("""
Learn about the different AWS S3 storage classes and write a blog on their use cases and benefits.

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_aws-s3-cloudcomputing-activity-7360044366083641344-5YDX?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
            """)
elif workspace == "Major Projects":
    st.title("🚀 Major Projects")
    major_tasks = [
        {
            "title": "Cache Memory Retrieve Data (Microservices)",
            "desc": "Designed a microservices backend with two Flask services, PostgreSQL for storage, Redis for caching, and Docker Compose for orchestration. Focused on real-world data flow and service interaction.)",
            "icon": "🗄️",
        },
        {
            "title": "Cache Memory Retrieve Data (DevOps/Kubernetes/Jenkins)",
            "desc": "Designed a microservices backend with two Flask services, PostgreSQL for storage, Redis for caching, and Docker Compose for orchestration. Focused on real-world data flow and service interaction.)",
            "icon": "🗄️",
        },
        {
            "title": "End-to-end CI/CD Pipeline with Jenkins, Docker, and GitHub",
            "desc": "Complete automation from code to deployment using Jenkins, Docker, and GitHub.)",
            "icon": "🚀",
        },
        {
            "title": "Menu Based Project - CommandHub",
            "desc": "A Python menu tool to run Linux commands on a remote RHEL root account via SSH. Built for convenience and learning, with code in Jupyter Notebook and easy expansion for more commands.)",
            "icon": "📋",
        },
        {
            "title": "Set up and configure the Apache webserver in Docker: Deploy and test Apache within a Docker environment",
            "desc": "Hands-on: Run, configure, and test Apache webserver in Docker. )",
            "icon": "🅰️",
        }
    ]
    st.markdown("""
    <style>
    .major-card-btn {
        background: #23242a;
        border-radius: 10px;
        box-shadow: 0 2px 12px #0003;
        padding: 18px 22px;
        margin: 18px auto;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        transition: box-shadow 0.2s;
        border: none;
        width: 340px;
        max-width: 96vw;
        min-width: 180px;
        height: 70px;
        text-align: center;
    }
    .major-card-btn:hover {
        box-shadow: 0 4px 16px #3c8cff88;
        background: #23244a;
    }
    .major-card-btn .icon {
        font-size: 1.5rem;
        margin-right: 12px;
    }
    .major-card-btn .title {
        font-size: 1.08rem;
        font-weight: 700;
        color: #fff;
        margin-bottom: 2px;
    }
    .major-card-btn .desc {
        font-size: 0.95rem;
        color: #ccc;
        margin-bottom: 0;
    }
    </style>
    """, unsafe_allow_html=True)
    selected_major_idx = st.session_state.get("major_selected_idx", None)
    for idx, task in enumerate(major_tasks):
        card_label = f"{task['icon']}  {task['title']}\n{task['desc']}"
        if st.button(card_label, key=f"major_card_{idx}"):
            selected_major_idx = idx
            st.session_state["major_selected_idx"] = idx
    if selected_major_idx is not None:
        if selected_major_idx == 0:
            st.header("Cache Memory Retrieve Data – Microservices Architecture")
            st.markdown("""
**Designed a microservices backend with two Flask services, PostgreSQL for storage, Redis for caching, and Docker Compose for orchestration. Focused on real-world data flow and service interaction.**

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_microservicesarchitecture-redis-postgresql-activity-7360041385988689920-4Wh2?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
            """)
        elif selected_major_idx == 1:
            st.header("Cache Memory Retrieve Data – DevOps/Kubernetes/Jenkins")
            st.markdown("""
**Designed a microservices backend with two Flask services, PostgreSQL for storage, Redis for caching, and Docker Compose for orchestration. Focused on real-world data flow and service interaction.**

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_devops-kubernetes-jenkins-activity-7360043080651075585-3aJT?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
            """)
        elif selected_major_idx == 2:
            st.header("End-to-end CI/CD Pipeline with Jenkins, Docker, and GitHub")
            st.markdown("""
**Complete automation from code to deployment using Jenkins, Docker, and GitHub.**

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_linuxworldinternship-day30-devopsjourney-activity-7349915001035386880-m9TY?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
            """)
        elif selected_major_idx == 3:
            st.header("Menu Based Project - CommandHub")
            st.markdown("""
**A Python menu tool to run Linux commands on a remote RHEL root account via SSH. Built for convenience and learning, with code in Jupyter Notebook and easy expansion for more commands.**

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_day17-linuxworld-vimaldagasir-activity-7341551758977978368-LAbM?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
            """)
        elif selected_major_idx == 4:
            st.header("Set up and configure the Apache webserver in Docker")
            st.markdown("""
**Hands-on: Run, configure, and test Apache webserver in Docker.**

Read the full post on LinkedIn:
[View Post](https://www.linkedin.com/posts/bhoomi-khandelwal-3a2918290_apacheserver-docker-devops-activity-7360003546869608450-MO7N?utm_source=share&utm_medium=member_android&rcm=ACoAAEaddSoBaebMLM5tMbqQP1Q8OsrIc0pft4w)
            """)
elif workspace == "Minor Projects":
    st.title("🧩 Minor Projects")
    st.markdown("""
### Code to Story Teller
In this project, you can write code and, for understanding code, you can convert it into a story. There is also an option to generate stories in multiple languages.

**Tech Stack:**
- Python
- NLP
- Streamlit
- Gradio

**GitHub:** [Code to Story Teller by Bhoomi0555](https://github.com/Bhoomi0555/code-to-story-teller)
    """)
    st.markdown("""
---
### AI DJ
You can control music with your finger gestures, like pause, stop, rewind, and play music.

**Tech Stack:**
- AI
- Gesture Recognition
- Music Control
- Python
- Streamlit

**GitHub:** [AI DJ by Bhoomi0555](https://github.com/Bhoomi0555/AI-DJ)
    """)
    
    st.markdown("""
---
### Woman Safety Portal
A Streamlit project for women safety. At night, you can send a message and connect with your loved ones, and there is an emergency number as well.

** Tech Stack:**
- Python
- Streamlit

**GitHub:** [Woman Safety Portal by Bhoomi0555](https://github.com/Bhoomi0555/women-portal-safety)
    """)
    
    st.markdown("""
---
### ayurveda assistent
A Streamlit project for Ayurveda. You can search for Ayurvedic medicines and their uses.

**Tech Stack:**
- Python
- Streamlit

**GitHub:** [Ayurveda Assistant by Bhoomi0555](https://github.com/Bhoomi0555/ayurveda-project)
    """)
