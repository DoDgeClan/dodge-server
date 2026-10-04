"""Balance and authoritative regressions; no fake multiplayer clients."""
import os,sys,pathlib,unittest,time
from unittest.mock import patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from combat_projectiles import Boomerang,segment_distance
from multiplayer_ws import MatchHub

class CombatTests(unittest.TestCase):
 def test_outbound_distance_independent_of_fps(self):
  positions=[]
  for fps in (30,60,120):
   b=Boomerang(0,0,1,0)
   for _ in range(fps//2):b.step(1/fps,(0,0))
   positions.append(b.x)
  for x in positions:self.assertAlmostEqual(x,200,places=6)
 def test_hit_once_per_leg_and_returns_to_moving_owner(self):
  b=Boomerang(0,0,1,0);hits=0
  for _ in range(240):
   if b.dead:break
   segment=b.step(1/120,(20,0))
   if b.hit('enemy',100,0,12,segment):hits+=1
  self.assertEqual(hits,2);self.assertTrue(b.dead);self.assertLess(b.age,1.1)
 def test_swept_hit_not_tunneled(self):
  self.assertEqual(segment_distance(100,0,0,0,200,0),0)
 def room(self):
  h=MatchHub();h.join('r','host',started=True);h.join('r','friend');r=h.rooms['r'];r['next_enemy']=time.monotonic()+100;r['enemies']=[];return h,r
 def test_authority_cooldown_and_single_kill_credit(self):
  h,r=self.room();p=r['players']['host'];p['x']=.3;p['y']=.5
  r['enemies']=[{'id':1,'x':.45,'y':.5,'type':'tank','hp':2,'max_hp':2}]
  h.action('r','host',{'action':'attack','mode':'boomerang'});h.action('r','host',{'action':'attack','mode':'boomerang'})
  self.assertEqual(len(r['boomerangs']),1)
  for _ in range(60):h._combat_tick(r,1/60,time.monotonic())
  self.assertEqual(p['kills'],1);self.assertEqual(r['players']['friend']['kills'],0);self.assertFalse(r['enemies'])
 def test_reflection_owner_no_self_damage_no_repeat_reward(self):
  h,r=self.room();now=time.monotonic();p=r['players']['host'];p.update(shield_until=now+3)
  r['bullets']=[{'x':.56,'y':.5,'vx':-.2,'vy':0,'until':now+7,'owner':None}]
  h._combat_tick(r,.05,now);self.assertEqual(p['hp'],3);self.assertEqual(p['shield_until'],0);self.assertEqual(r['bullets'][0]['owner'],'host')
  r['enemies']=[{'id':1,'x':.66,'y':.5,'type':'normal','hp':1,'max_hp':1}]
  for _ in range(40):h._combat_tick(r,.05,now)
  self.assertEqual(p['kills'],1);self.assertFalse(r['bullets']);self.assertEqual(p['hp'],3)
 def test_dead_player_cannot_launch(self):
  h,r=self.room();r['players']['host']['hp']=0;h.action('r','host',{'action':'attack','mode':'boomerang'});self.assertEqual(r['boomerangs'],[])
if __name__=='__main__':unittest.main()
