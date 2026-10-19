// Maintenance variant of ns3_multiservice_online.cc (2026-09-30): identical except that --maintenance="path,start,end,bps;..."
// caps the capacity of a bottleneck path during decision intervals [start, end) (loop ticks). The original file is unchanged.
// Independent eight-node shared-bottleneck experiment. No historical source changed.
// S_query,S_video,S_backup -> R -> A/B/C -> D. Per-destination routes at R.
// Application UID accounting measures actual payload delivery, delay and drops.
#include "ns3/applications-module.h"
#include "ns3/core-module.h"
#include "ns3/internet-module.h"
#include "ns3/network-module.h"
#include "ns3/point-to-point-module.h"
#include "ns3/traffic-control-module.h"
#include <array>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <unordered_map>
#include <unordered_set>
#include <vector>
#include <unistd.h>
using namespace ns3;

static const std::array<std::string,3> names={"query","video","backup"};
static const std::array<uint64_t,3> demand={2000000,4000000,6000000};
static const std::array<double,3> delays={5,15,30};
static std::array<int,3> routes={3,2,3};
static std::array<uint64_t,3> caps={0,0,0};
static std::array<Ipv4Address,3> destinations,hops;
static std::array<uint32_t,3> interfaces;
static Ptr<Ipv4StaticRouting> routing;
static std::array<Ptr<Application>,3> apps;
static std::array<Ptr<PointToPointNetDevice>,3> devices;
static std::array<Ptr<QueueDisc>,3> queues;
static std::array<std::unordered_map<uint64_t,double>,3> sent;
static std::array<std::unordered_set<uint64_t>,3> received;
static std::unordered_set<uint64_t> dropped;
static std::array<uint64_t,3> bytes={},lastBytes={},lastRx={},lastDrop={},errors={};
static std::array<double,3> delaySum={},lastDelay={};
static uint32_t edits=0,routeEdits=0,rateEdits=0;
static std::string directory;

static void Tx(uint32_t i,Ptr<const Packet> p) {
    if(!sent[i].emplace(p->GetUid(),Simulator::Now().GetSeconds()).second) ++errors[i];
}
static void Rx(uint32_t i,Ptr<const Packet> p,const Address&) {
    auto it=sent[i].find(p->GetUid());
    if(it==sent[i].end() || !received[i].insert(p->GetUid()).second) {++errors[i];return;}
    bytes[i]+=p->GetSize();delaySum[i]+=(Simulator::Now().GetSeconds()-it->second)*1000;
}
static void Drop(Ptr<const Packet> p) {dropped.insert(p->GetUid());}
static void QDrop(Ptr<const QueueDiscItem> p) {Drop(p->GetPacket());}
static void SetRoute(uint32_t i,int path) {
    for(int j=(int)routing->GetNRoutes()-1;j>=0;--j)
        if(routing->GetRoute(j).GetDest()==destinations[i]) routing->RemoveRoute(j);
    routing->AddHostRouteTo(destinations[i],hops[path-1],interfaces[path-1]);routes[i]=path;
}
static void SetCap(uint32_t i,uint64_t cap) {
    caps[i]=cap;
    apps[i]->SetAttribute("DataRate",DataRateValue(DataRate(cap?std::min(cap,demand[i]):demand[i])));
}
static void Publish(const std::string& name,const std::string& value) {
    std::string tmp=directory+"/"+name+".tmp",dst=directory+"/"+name+".json";
    {std::ofstream f(tmp);f<<value<<"\n";}
    if(rename(tmp.c_str(),dst.c_str())) std::exit(3);
}
static void WriteConfig(std::ostream& s) {
    s<<"\"paths\":["<<routes[0]<<","<<routes[1]<<","<<routes[2]<<"],\"caps_bps\":["
      <<caps[0]<<","<<caps[1]<<","<<caps[2]<<"],\"edits\":"<<edits
      <<",\"route_edits\":"<<routeEdits<<",\"rate_edits\":"<<rateEdits;
}
static void Observe(uint32_t tick) {
    std::ostringstream s;s<<std::setprecision(17)<<"{\"tick\":"<<tick<<",\"physical_time\":"
        <<Simulator::Now().GetSeconds()<<",";WriteConfig(s);
    s<<",\"links\":[";
    for(uint32_t i=0;i<3;++i) {
        if(i)s<<",";DataRateValue rate;devices[i]->GetAttribute("DataRate",rate);
        s<<"{\"id\":"<<i+1<<",\"capacity_mbps\":"<<rate.Get().GetBitRate()/1e6
          <<",\"delay_ms\":"<<delays[i]<<",\"queued_bytes\":"
          <<queues[i]->GetNBytes()+devices[i]->GetQueue()->GetNBytes()<<"}";
    }
    s<<"],\"services\":[";
    for(uint32_t i=0;i<3;++i) {
        uint64_t ndrop=0,pending=0;double age=0;
        for(auto& p:sent[i]) {
            bool r=received[i].count(p.first),d=dropped.count(p.first);
            if(r&&d)++errors[i];
            if(!r&&d)++ndrop;
            if(!r&&!d){++pending;age=std::max(age,(Simulator::Now().GetSeconds()-p.second)*1000);}
        }
        uint64_t rx=received[i].size()-lastRx[i],drop=ndrop-lastDrop[i];
        if(i)s<<",";
        DataRateValue actualRate;apps[i]->GetAttribute("DataRate",actualRate);
        s<<"{\"name\":\""<<names[i]<<"\",\"demand_mbps\":"<<demand[i]/1e6
          <<",\"offered_mbps\":"<<actualRate.Get().GetBitRate()/1e6
          <<",\"throughput_mbps\":"<<(bytes[i]-lastBytes[i])*8/1e6
          <<",\"delay_ms\":"<<(rx?(delaySum[i]-lastDelay[i])/rx:1000)
          <<",\"loss\":"<<(rx+drop?(double)drop/(rx+drop):1)
          <<",\"tx\":"<<sent[i].size()<<",\"rx\":"<<received[i].size()
          <<",\"dropped\":"<<ndrop<<",\"pending\":"<<pending<<",\"pending_max_age_ms\":"<<age
          <<",\"rx_bytes\":"<<bytes[i]<<",\"delay_sum_ms\":"<<delaySum[i]
          <<",\"errors\":"<<errors[i]<<"}";
        lastRx[i]=received[i].size();lastDrop[i]=ndrop;lastBytes[i]=bytes[i];lastDelay[i]=delaySum[i];
    }
    s<<"]}";Publish("obs_"+std::to_string(tick),s.str());
}
static void Tick(uint32_t tick,uint32_t n) {
    Observe(tick);if(tick==n)return;
    std::string file=directory+"/act_"+std::to_string(tick)+".txt";
    int waited=0;while(access(file.c_str(),F_OK)!=0) {usleep(1000);if(++waited>30000)std::exit(5);}
    uint32_t frame;std::array<int,3> p;std::array<uint64_t,3> c;
    std::ifstream f(file);f>>frame>>p[0]>>p[1]>>p[2]>>c[0]>>c[1]>>c[2];
    if(!f||frame!=tick)std::exit(6);
    int changes=0;
    for(int i=0;i<3;++i) {
        if(p[i]<1||p[i]>3||c[i]>demand[i]||c[i]%1000)std::exit(7);
        changes+=(p[i]!=routes[i])+(c[i]!=caps[i]);
    }
    if(changes>1)std::exit(8);
    for(int i=0;i<3;++i) {
        if(p[i]!=routes[i]){SetRoute(i,p[i]);++routeEdits;}
        if(c[i]!=caps[i]){SetCap(i,c[i]);++rateEdits;}
    }
    edits+=changes;std::ostringstream s;s<<"{\"tick\":"<<tick<<",";WriteConfig(s);s<<"}";
    Publish("ack_"+std::to_string(tick),s.str());
    Simulator::Schedule(Seconds(1),&Tick,tick+1,n);
}
struct Maintenance {int path;uint32_t start,end;uint64_t bps;};
static std::vector<Maintenance> maintenance;
static void ParseMaintenance(const std::string& spec) {
    std::stringstream all(spec);std::string one;
    while(std::getline(all,one,';')) {
        if(one.empty())continue;
        std::stringstream f(one);std::string a,b,c,d;
        if(!std::getline(f,a,',')||!std::getline(f,b,',')||!std::getline(f,c,',')||!std::getline(f,d,','))std::exit(9);
        Maintenance m{std::stoi(a),(uint32_t)std::stoul(b),(uint32_t)std::stoul(c),(uint64_t)std::stoull(d)};
        if(m.path<1||m.path>3||m.end<=m.start||m.bps<100000)std::exit(9);
        maintenance.push_back(m);
    }
}
static void Event(uint32_t t,uint32_t seed) {
    // Independently phased capacity variation; maintenance windows cap a path's capacity. No future event is
    // supplied to the policy by the simulator: the only advance information is whatever the policy is told.
    const std::array<uint64_t,3> low={4500000,7000000,9000000},high={6500000,9000000,10000000};
    for(int i=0;i<3;++i) {
        uint64_t rate=((t+seed%7+i*3)%(9+2*i)<3)?low[i]:high[i];
        // Event(t) sets the rate for simulated second [t, t+1), i.e. the interval following decision t-1.
        for(const auto& m:maintenance) if(m.path==i+1 && t>=1 && t-1>=m.start && t-1<m.end) rate=std::min(rate,m.bps);
        devices[i]->SetDataRate(DataRate(rate));
    }
}
int main(int argc,char** argv) {
    uint32_t seed=704900,n=24;
    CommandLine cmd;cmd.AddValue("rundir","Existing unique IPC directory",directory);
    std::string maintenanceSpec;
    cmd.AddValue("seed","RNG run",seed);cmd.AddValue("decisions","Decisions",n);
    cmd.AddValue("maintenance","path,start,end,bps;... capacity caps in decision intervals [start,end)",maintenanceSpec);cmd.Parse(argc,argv);
    ParseMaintenance(maintenanceSpec);
    if(directory.empty()||n>24||n<1)return 2;
    RngSeedManager::SetSeed(1);RngSeedManager::SetRun(seed);
    NodeContainer nodes;nodes.Create(8);InternetStackHelper internet;internet.Install(nodes);
    auto R=nodes.Get(3),D=nodes.Get(7);Ipv4StaticRoutingHelper helper;
    routing=helper.GetStaticRouting(R->GetObject<Ipv4>());
    PointToPointHelper accessLink;accessLink.SetDeviceAttribute("DataRate",StringValue("100Mbps"));
    accessLink.SetChannelAttribute("Delay",StringValue("1ms"));
    std::array<Ipv4Address,3> sourceGate,branchDest;
    Ipv4AddressHelper ip;
    for(int i=0;i<3;++i) {
        auto dev=accessLink.Install(nodes.Get(i),R);
        std::string subnet="10.1."+std::to_string(i+1)+".0";
        ip.SetBase(subnet.c_str(),"255.255.255.0");auto addr=ip.Assign(dev);sourceGate[i]=addr.GetAddress(1);
    }
    for(int i=0;i<3;++i) {
        auto branch=nodes.Get(4+i);auto a=accessLink.Install(R,branch);
        std::string subnet="10.2."+std::to_string(i+1)+".0";
        ip.SetBase(subnet.c_str(),"255.255.255.0");auto aa=ip.Assign(a);
        hops[i]=aa.GetAddress(1);interfaces[i]=R->GetObject<Ipv4>()->GetInterfaceForDevice(a.Get(0));
        PointToPointHelper bottleneck;bottleneck.SetDeviceAttribute("DataRate",StringValue("10Mbps"));
        bottleneck.SetChannelAttribute("Delay",TimeValue(MilliSeconds(delays[i])));
        bottleneck.SetQueue("ns3::DropTailQueue<Packet>","MaxSize",StringValue("50p"));
        auto b=bottleneck.Install(branch,D);
        devices[i]=DynamicCast<PointToPointNetDevice>(b.Get(0));
        TrafficControlHelper tc;tc.SetRootQueueDisc("ns3::FifoQueueDisc","MaxSize",StringValue("50p"));
        queues[i]=tc.Install(b.Get(0)).Get(0);
        // Address assignment installs a default qdisc if none exists: install ours first.
        subnet="10.3."+std::to_string(i+1)+".0";
        ip.SetBase(subnet.c_str(),"255.255.255.0");auto ba=ip.Assign(b);
        destinations[i]=ba.GetAddress(1);branchDest[i]=ba.GetAddress(1);
    }
    for(int i=0;i<3;++i) {
        auto branch=nodes.Get(4+i);auto sr=helper.GetStaticRouting(branch->GetObject<Ipv4>());
        for(int j=0;j<3;++j)sr->AddHostRouteTo(destinations[j],branchDest[i],2);
        helper.GetStaticRouting(nodes.Get(i)->GetObject<Ipv4>())->AddHostRouteTo(destinations[i],sourceGate[i],1);
        SetRoute(i,routes[i]);
        OnOffHelper app("ns3::UdpSocketFactory",InetSocketAddress(destinations[i],9000+i));
        app.SetAttribute("OnTime",StringValue("ns3::ConstantRandomVariable[Constant=1]"));
        app.SetAttribute("OffTime",StringValue("ns3::ConstantRandomVariable[Constant=0]"));
        app.SetAttribute("DataRate",DataRateValue(DataRate(demand[i])));app.SetAttribute("PacketSize",UintegerValue(1200));
        auto ac=app.Install(nodes.Get(i));ac.Start(Seconds(0));ac.Stop(Seconds(n+1.001));apps[i]=ac.Get(0);
        NS_ABORT_MSG_IF(!apps[i]->TraceConnectWithoutContext("Tx",MakeBoundCallback(&Tx,(uint32_t)i)),"Tx trace");
        PacketSinkHelper sink("ns3::UdpSocketFactory",InetSocketAddress(Ipv4Address::GetAny(),9000+i));
        auto sc=sink.Install(D);sc.Start(Seconds(0));sc.Stop(Seconds(n+1.001));
        NS_ABORT_MSG_IF(!sc.Get(0)->TraceConnectWithoutContext("Rx",MakeBoundCallback(&Rx,(uint32_t)i)),"Rx trace");
    }
    for(uint32_t i=0;i<nodes.GetN();++i)for(uint32_t j=0;j<nodes.Get(i)->GetNDevices();++j) {
        auto dev=DynamicCast<PointToPointNetDevice>(nodes.Get(i)->GetDevice(j));if(!dev)continue;
        dev->TraceConnectWithoutContext("PhyRxDrop",MakeCallback(&Drop));
        dev->TraceConnectWithoutContext("MacTxDrop",MakeCallback(&Drop));
        dev->GetQueue()->TraceConnectWithoutContext("Drop",MakeCallback(&Drop));
    }
    for(auto q:queues)q->TraceConnectWithoutContext("Drop",MakeCallback(&QDrop));
    for(uint32_t t=0;t<=n+1;++t)Simulator::Schedule(Seconds(t),&Event,t,seed);
    Simulator::Schedule(Seconds(1),&Tick,0,n);Simulator::Stop(Seconds(n+1.001));
    Simulator::Run();Simulator::Destroy();return 0;
}
