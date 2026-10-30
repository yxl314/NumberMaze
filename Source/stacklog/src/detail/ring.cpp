#include <stacklog/detail/ring.hpp>

namespace stacklog
{
namespace detail
{
namespace
{

std::size_t round_up_pow2(std::size_t v_value)
{
    std::size_t result = 1;
    while (result < v_value)
    {
        result <<= 1;
    }
    return result;
}

} // namespace

Ring::Ring(std::size_t v_capacity): m_mask(0), m_head(0), m_tail(0), m_dead(false)
{
    const std::size_t rounded = round_up_pow2(v_capacity == 0 ? 1 : v_capacity);
    m_slots.resize(rounded);
    m_mask = rounded - 1;
}

Ring::~Ring() {}

Record* Ring::reserve()
{
    const std::uint64_t head = m_head.load(std::memory_order_relaxed);
    const std::uint64_t tail = m_tail.load(std::memory_order_acquire);
    if (head - tail >= m_slots.size())
    {
        return 0;
    }
    return &m_slots[static_cast<std::size_t>(head) & m_mask];
}

void Ring::commit()
{
    m_head.store(m_head.load(std::memory_order_relaxed) + 1, std::memory_order_release);
}

bool Ring::pop(Record& v_out)
{
    const std::uint64_t tail = m_tail.load(std::memory_order_relaxed);
    if (tail == m_head.load(std::memory_order_acquire))
    {
        return false;
    }
    v_out = m_slots[static_cast<std::size_t>(tail) & m_mask];
    m_tail.store(tail + 1, std::memory_order_release);
    return true;
}

bool Ring::drained() const
{
    return m_tail.load(std::memory_order_relaxed) == m_head.load(std::memory_order_relaxed);
}

void Ring::mark_dead()
{
    m_dead.store(true, std::memory_order_release);
}

bool Ring::dead() const
{
    return m_dead.load(std::memory_order_acquire);
}

} // namespace detail
} // namespace stacklog
