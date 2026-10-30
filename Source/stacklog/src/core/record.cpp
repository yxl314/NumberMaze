#include <stacklog/core/record.hpp>

namespace stacklog
{

const char* level_name(Level v_level)
{
    switch (v_level)
    {
    case Level::Debug:
        return "debug";
    case Level::Info:
        return "info";
    case Level::Warn:
        return "warn";
    case Level::Error:
        return "error";
    }
    return "unknown";
}

const char* kind_name(Kind v_kind)
{
    switch (v_kind)
    {
    case Kind::Enter:
        return "enter";
    case Kind::Exit:
        return "exit";
    case Kind::Event:
        return "event";
    }
    return "unknown";
}

} // namespace stacklog
