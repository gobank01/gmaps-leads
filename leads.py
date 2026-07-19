#!/usr/bin/env python3
"""
gmaps-leads — ดึง lead B2B จาก Google Maps ระดับพันรายการ
หลักการ: fan-out (คำค้น × พื้นที่) + dedup (place_id)

ใช้:
  python3 leads.py "ร้านขายยา" --area bkk              # 50 เขต กทม.
  python3 leads.py "โรงงาน" --area provinces           # 77 จังหวัด
  python3 leads.py "คลินิกความงาม" --area "บางนา,อโศก,ทองหล่อ"
  เพิ่ม --email = ดึงอีเมลจากเว็บร้านด้วย (ช้าลงมาก) · --depth 5 = จำนวนต่อคำค้น

ต้องมีอย่างใดอย่างหนึ่ง:
  1. Docker (แนะนำสำหรับนักเรียน)  docker.com/products/docker-desktop
  2. binary google-maps-scraper (ตั้ง env GMAPS_BIN ชี้ไปที่ไฟล์)
"""
import argparse, csv, os, shutil, subprocess, sys, tempfile, time
from datetime import date

BKK = ["พระนคร","ดุสิต","หนองจอก","บางรัก","บางเขน","บางกะปิ","ปทุมวัน","ป้อมปราบศัตรูพ่าย","พระโขนง","มีนบุรี","ลาดกระบัง","ยานนาวา","สัมพันธวงศ์","พญาไท","ธนบุรี","บางกอกใหญ่","ห้วยขวาง","คลองสาน","ตลิ่งชัน","บางกอกน้อย","บางขุนเทียน","ภาษีเจริญ","หนองแขม","ราษฎร์บูรณะ","บางพลัด","ดินแดง","บึงกุ่ม","สาทร","บางซื่อ","จตุจักร","บางคอแหลม","ประเวศ","คลองเตย","สวนหลวง","จอมทอง","ดอนเมือง","ราชเทวี","ลาดพร้าว","วัฒนา","บางแค","หลักสี่","สายไหม","คันนายาว","สะพานสูง","วังทองหลาง","คลองสามวา","บางนา","ทวีวัฒนา","ทุ่งครุ","บางบอน"]
PROVINCES = ["กรุงเทพ","สมุทรปราการ","นนทบุรี","ปทุมธานี","พระนครศรีอยุธยา","อ่างทอง","ลพบุรี","สิงห์บุรี","ชัยนาท","สระบุรี","ชลบุรี","ระยอง","จันทบุรี","ตราด","ฉะเชิงเทรา","ปราจีนบุรี","นครนายก","สระแก้ว","นครราชสีมา","บุรีรัมย์","สุรินทร์","ศรีสะเกษ","อุบลราชธานี","ยโสธร","ชัยภูมิ","อำนาจเจริญ","บึงกาฬ","หนองบัวลำภู","ขอนแก่น","อุดรธานี","เลย","หนองคาย","มหาสารคาม","ร้อยเอ็ด","กาฬสินธุ์","สกลนคร","นครพนม","มุกดาหาร","เชียงใหม่","ลำพูน","ลำปาง","อุตรดิตถ์","แพร่","น่าน","พะเยา","เชียงราย","แม่ฮ่องสอน","นครสวรรค์","อุทัยธานี","กำแพงเพชร","ตาก","สุโขทัย","พิษณุโลก","พิจิตร","เพชรบูรณ์","ราชบุรี","กาญจนบุรี","สุพรรณบุรี","นครปฐม","สมุทรสาคร","สมุทรสงคราม","เพชรบุรี","ประจวบคีรีขันธ์","นครศรีธรรมราช","กระบี่","พังงา","ภูเก็ต","สุราษฎร์ธานี","ระนอง","ชุมพร","สงขลา","สตูล","ตรัง","พัทลุง","ปัตตานี","ยะลา","นราธิวาส"]

def scraper_cmd(workdir, extra):
    binp = os.environ.get("GMAPS_BIN")
    if binp and os.path.exists(binp):
        return [binp, "-input", f"{workdir}/queries.txt", "-results", f"{workdir}/raw.csv"] + extra
    if shutil.which("docker"):
        return ["docker", "run", "--rm",
                "-v", "gmaps-playwright-cache:/opt",  # cache browser ไม่ต้องโหลดใหม่ทุกรอบ
                "-v", f"{workdir}:/gmapsdata",
                "gosom/google-maps-scraper",
                "-input", "/gmapsdata/queries.txt", "-results", "/gmapsdata/raw.csv"] + extra
    sys.exit("ไม่พบ Docker และไม่ได้ตั้ง GMAPS_BIN — ติดตั้ง Docker Desktop ก่อน (docker.com)")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword")
    ap.add_argument("--area", default="bkk", help="bkk | provinces | รายชื่อคั่น comma")
    ap.add_argument("--depth", type=int, default=5, help="ยิ่งมากยิ่งได้เยอะ/ช้า (5≈60ราย/คำค้น)")
    ap.add_argument("--email", action="store_true", help="ดึงอีเมลจากเว็บร้าน (ช้าลงมาก)")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    areas = {"bkk": BKK, "provinces": PROVINCES}.get(a.area) or [s.strip() for s in a.area.split(",")]
    # workdir อยู่ใน cwd ไม่ใช่ system temp — Docker Desktop mount /var/folders ไม่ได้
    workdir = tempfile.mkdtemp(prefix="gmaps-work-", dir=os.getcwd())
    with open(f"{workdir}/queries.txt", "w") as f:
        f.writelines(f"{a.keyword} {area}\n" for area in areas)
    est = len(areas) * a.depth * 12 / 15 / 60  # วัดจริง ~12 ราย/depth/คำค้น ที่ ~15 ราย/นาที
    print(f"คำค้น {len(areas)} ชุด · depth {a.depth} · email {'on' if a.email else 'off'} · ประมาณ {est:.1f} ชม. (รันทิ้งไว้ได้)")

    extra = ["-lang", "th", "-depth", str(a.depth), "-c", "6", "-exit-on-inactivity", "3m"]
    if a.email:
        extra.append("-email")
    # ponytail: -exit-on-inactivity ของ scraper แขวนได้ (เจอจริง) → watchdog ฆ่าเองเมื่อ raw.csv นิ่งเกิน 5 นาที
    raw = f"{workdir}/raw.csv"
    p = subprocess.Popen(scraper_cmd(workdir, extra))
    while p.poll() is None:
        time.sleep(30)
        if os.path.exists(raw) and time.time() - os.path.getmtime(raw) > 300:
            print("ข้อมูลนิ่ง 5 นาที — ปิด scraper เอง")
            p.terminate()
            break
    p.wait()

    # dedup ด้วย place_id (fallback: ชื่อ+ที่อยู่)
    seen, rows = set(), []
    with open(f"{workdir}/raw.csv", newline="") as f:
        for r in csv.DictReader(f):
            key = r.get("place_id") or (r.get("title", "") + r.get("address", ""))
            if key and key not in seen:
                seen.add(key)
                rows.append(r)

    if not rows:
        sys.exit(f"ได้ 0 ราย — ลองคำค้นกว้างขึ้น หรือดู log ดิบที่ {workdir}")
    out = a.out or f"{a.keyword.replace(' ', '-')}-{date.today()}.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    ph = sum(1 for r in rows if r.get("phone"))
    wb = sum(1 for r in rows if r.get("website"))
    em = sum(1 for r in rows if r.get("emails"))
    shutil.rmtree(workdir)
    print(f"\n✅ {out}\nรวม {len(rows)} ราย (หลังตัดซ้ำ) · โทร {ph} · เว็บ {wb} · อีเมล {em}")

if __name__ == "__main__":
    main()
