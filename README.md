NOTE: This project is more made to familiarize myself with actively using AWS. Without an AWS account, most features still work, but the persistance and link to cw won't.
The more astute amongst readers might ask "Wait, does CW even do anything in this project?" The answer is no, I planned to add a dashboard with graphs until I realized
that it was paid, but at least I now have experience publishing metrics to CW. 
<br><br>
Anyways, here is the actual project:<br>
Features:<br>
From the Base BTM,<br>
Kill processes by name via search, and destroy multiple at a time<br>
CPU, Ram, disk, and network real time monitoring<br>
NEW with this:<br>
Tracks the top processes for CPU, ram, and disk usage<br>
Publishes these metrics to CW every 60 seconds<br>
Also sends them to DynamoDB, which is whre the persistance happens; see usage statistics from past sessions.<br>

tech stack:<br>
python, tkinter, psutil, boto3, threading (idk if this goes here, but I had to learn for it so I'm putting it here) <br>
AWS: CloudWatch, DynamoDB<br>

Setup:<br>
Install dependencies: pip install -r requirements.txt<br>
(if you don't want the AWS functionality, feel free to skip these next two steps)<br>
aws configure<br>
python aws_setup.py<br>
python tkinterPracticeTwo.py<br>

Architecture:<br>
A background daemon thread collects system stats every 60 seconds and pushes them to CloudWatch and DynamoDB. The UI polls an in-memory buffer every 3 seconds, soit never touches AWS directly, keeping the interface responsive at all times. To be honest I figured the architecture out kinda after pushing metrics to CW and DDB, so if I ever have money I'll add more functionality with them, because as of now only DDB actually does anything (I don't count my link to CW metrics as a real feature).
