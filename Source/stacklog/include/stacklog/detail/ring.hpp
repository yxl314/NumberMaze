#ifndef INCLUDE_STACKLOG_DETAIL_RING_HPP
#define INCLUDE_STACKLOG_DETAIL_RING_HPP

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <vector>

#include <stacklog/core/record.hpp>

namespace stacklog
{
namespace detail
{

/// 每线程一个的单生产者单消费者环形缓冲。
///
/// 生产者在自己的线程上 reserve/commit，消费者由排空机制串行推进；
/// 同一时刻只允许一个消费者，靠排空路径上的互斥量保证。
class Ring
{
public:
    explicit Ring(std::size_t v_capacity);
    ~Ring();

    /// 生产者：占一个槽位并返回可写指针；缓冲已满返回 0。
    Record* reserve();

    /// 生产者：发布上一笔 reserve 占用的槽位。
    void    commit();

    /// 消费者：取出一条记录；无记录返回 false。
    bool    pop(Record& v_out);

    /// 消费者：缓冲是否已取空。
    bool    drained() const;

    /// 标记所属线程已退出；内存由排空机制回收。
    void    mark_dead();
    bool    dead() const;

private:
    Ring(const Ring&)                                 = delete;
    Ring&                      operator=(const Ring&) = delete;

    std::vector<Record>        m_slots;
    std::size_t                m_mask; // 容量向上取到 2 的幂后的掩码
    std::atomic<std::uint64_t> m_head; // 已提交条数，生产者写
    std::atomic<std::uint64_t> m_tail; // 已消费条数，消费者写
    std::atomic<bool>          m_dead;
};

} // namespace detail
} // namespace stacklog

#endif // INCLUDE_STACKLOG_DETAIL_RING_HPP
