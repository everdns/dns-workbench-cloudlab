python3 /local/repository/zone_generation/zone_generator.py --config /local/repository/zone_generation/config.json --out-dir ~/multi_record
cp ~/multi_record/dnsperf_input_10-0-0-0_65536 /local/repository/dnsperf_input
scp -r ~/multi_record/ 10.10.1.1:~/
ssh 10.10.1.1 'bash /local/repository/update_zone_files.sh ~/multi_record/db.workbench.lan'