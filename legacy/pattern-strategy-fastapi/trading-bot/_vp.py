import sys
import validate_patterns as VP
from marketdata.base import Bar
import os
# synthetic double-top series: two equal peaks with a valley, last bar a sell rejection
def B(o,h,l,c): return Bar("EURUSD","1h","t",o,h,l,c,1)
import math
bars=[]
for i in range(60):
    # base wave forming a double top near the end
    if i in (30,):   # peak 1
        bars.append(B(1.105,1.1080,1.104,1.1045))
    elif i==45:      # peak 2 (~equal)
        bars.append(B(1.105,1.1079,1.104,1.1042))
    else:
        mid=1.100+0.0005*math.sin(i/3)
        bars.append(B(mid,mid+0.0006,mid-0.0006,mid))
flags={"double_top":True,"double_bottom":True,"head_shoulders":True,"inverse_hs":True,"triple_top":True,"triple_bottom":True,"rectangle":True,"trendline":True}
det=VP.detect_history(bars, flags, min_bars=40)
print("detections:", [(i, s.setup, s.side) for i,s in det][:5])
os.makedirs("/tmp/_vptest", exist_ok=True)
if det:
    i,s=det[0]
    VP.render(bars, i, s, "/tmp/_vptest/sample.png")
    sz=os.path.getsize("/tmp/_vptest/sample.png")
    print("rendered sample.png bytes:", sz, "OK" if sz>3000 else "TOO SMALL")
else:
    print("no detections on synthetic (detector window may need a cleaner shape)")
