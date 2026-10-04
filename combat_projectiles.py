"""Bounded, time-based boomerang motion and swept collision, in caller units."""
import math

def segment_distance(px,py,ax,ay,bx,by):
    dx,dy=bx-ax,by-ay;den=dx*dx+dy*dy
    t=max(0,min(1,((px-ax)*dx+(py-ay)*dy)/den)) if den else 0
    return math.hypot(px-ax-t*dx,py-ay-t*dy)

class Boomerang:
    def __init__(self,x,y,dx,dy,scale=1):
        length=math.hypot(dx,dy) or 1
        self.x=x;self.y=y;self.dx=dx/length;self.dy=dy/length
        self.scale=scale;self.age=0;self.returning=False;self.hits=set();self.dead=False
    def step(self,dt,owner):
        previous=(self.x,self.y)
        outbound=min(dt,max(0,.55-self.age))
        self.x+=self.dx*400*self.scale*outbound;self.y+=self.dy*400*self.scale*outbound
        self.age+=dt
        if self.age>=.55 and not self.returning:self.returning=True;self.hits.clear()
        if self.returning:
            remaining=dt-outbound;dx,dy=owner[0]-self.x,owner[1]-self.y;distance=math.hypot(dx,dy)
            if distance<=500*self.scale*remaining+12*self.scale:self.dead=True
            elif distance:
                self.dx,self.dy=dx/distance,dy/distance
                self.x+=self.dx*500*self.scale*remaining;self.y+=self.dy*500*self.scale*remaining
        if self.age>=2:self.dead=True
        return previous,(self.x,self.y)
    def hit(self,identifier,x,y,radius,segment):
        if self.dead or identifier in self.hits:return False
        if segment_distance(x,y,*segment[0],*segment[1])<=radius+10*self.scale:
            self.hits.add(identifier);return True
        return False
